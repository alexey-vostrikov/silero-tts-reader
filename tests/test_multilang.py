#!/usr/bin/env python3
"""Многоязычность: выбор модели по языку, профили моделей и миграция конфига.

Тест не грузит настоящие модели (torch в образе нет и 139 МБ качать незачем):
загрузчик модели подменяется, поэтому проверяется логика выбора языка,
профиля и голоса, а не нейросеть.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from silero_tts_reader import models as models_module
from silero_tts_reader.config import config_manager as cfg_module
from silero_tts_reader.config.config_manager import ConfigManager
from silero_tts_reader.core.text_processor import TextProcessor
from silero_tts_reader.core.tts_engine import TTSEngine

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


class _Audio:
    """Заглушка торч-тензора: движку нужен только .numpy()."""

    def __init__(self, samples: int = 16) -> None:
        self._data = np.zeros(samples, dtype=np.float32)

    def numpy(self) -> np.ndarray:
        return self._data


class _StubModel:
    def __init__(self, speakers: list[str]) -> None:
        self.speakers = speakers
        self.calls: list[dict] = []

    def apply_tts(self, **kwargs):
        self.calls.append(kwargs)
        return _Audio()


def make_engine(models: dict[str, str], speakers: dict[str, list[str]]) -> TTSEngine:
    """Движок с подменённой загрузкой: реальные файлы моделей не нужны.

    Пути всё же должны существовать: движок решает, какая модель доступна, по
    своим путям (в CI настоящих файлов нет), и без файлов английский текст
    уходил бы в русскую модель.
    """
    engine = TTSEngine(sample_rate=48000, models=models)
    stubs = {lang: _StubModel(voices) for lang, voices in speakers.items()}

    def fake_load(lang: str) -> None:
        if lang not in stubs:
            raise AssertionError(f"неожиданный язык модели: {lang}")
        engine._models[lang] = stubs[lang]
        engine._speakers[lang] = list(stubs[lang].speakers)

    engine._load_model = fake_load  # type: ignore[method-assign]
    engine._stubs = stubs  # type: ignore[attr-defined]
    return engine


def _fake_model_files(langs: tuple[str, ...]) -> dict[str, str]:
    """Пустые файлы с именами моделей: движку важно только их наличие."""
    tmpdir = Path(tempfile.mkdtemp())
    result = {}
    for lang in langs:
        path = tmpdir / models_module.MODELS[lang].filename
        path.write_bytes(b"")
        result[lang] = str(path)
    return result


def test_detect() -> None:
    print("\n1. Определение языка по тексту")
    check(models_module.detect_language("привет мир") == "ru", "кириллица → ru")
    check(models_module.detect_language("hello world") == "en", "латиница → en")
    check(models_module.detect_language("Привет, Python!") == "ru", "смешанный текст → ru")
    check(models_module.detect_language("12345 ...") is None, "только цифры → язык не определён")


def test_resolve_with_installed() -> None:
    print("\n2. Явный язык и доступность модели")
    installed = {"ru": True, "en": True}

    def fake_installed(lang: str) -> bool:
        return installed.get(lang, False)

    saved = models_module.is_installed
    models_module.is_installed = fake_installed
    try:
        check(models_module.resolve_language("hello", "ru") == "ru", "--lang ru важнее текста")
        check(models_module.resolve_language("привет", "en") == "en", "--lang en важнее текста")
        check(models_module.resolve_language("привет", "en_GB") == "en", "код с регионом понимается")
        check(models_module.resolve_language("hello", "de") == "en",
              "неустановленный язык — откат на определённый по тексту")
        installed["en"] = False
        check(models_module.resolve_language("hello", None) == "ru",
              "без английской модели латиница идёт в русскую")
        check(models_module.resolve_language("hello", "en") == "ru",
              "явный --lang en без модели не ломает выбор")
    finally:
        models_module.is_installed = saved


def test_split_by_script() -> None:
    print("\n3. Разбиение по письменности")
    cases = {
        "synthesize использует пути движка": [("en", "synthesize"), ("ru", "использует пути движка")],
        "Hello world, это тест": [("en", "Hello world,"), ("ru", "это тест")],
        "только русский": [("ru", "только русский")],
        "only english": [("en", "only english")],
        "Привет! Hello world, как дела?": [
            ("ru", "Привет!"), ("en", "Hello world,"), ("ru", "как дела?"),
        ],
        "12345": [(None, "12345")],
    }
    for text, expected in cases.items():
        got = models_module.split_by_script(text)
        check(got == expected, f"{text!r} → {got}")

    # Подряд идущие слова одного языка — один кусок: иначе речь дробилась бы
    joined = models_module.split_by_script("Hello beautiful world")
    check(joined == [("en", "Hello beautiful world")], f"английская фраза целиком: {joined}")

    # Кириллица со вставкой латиницы: вставка становится отдельным куском
    mixed = models_module.split_by_script("Это Python код")
    check(mixed == [("ru", "Это"), ("en", "Python"), ("ru", "код")], f"вставка отдельно: {mixed}")


def test_mixed_synthesis() -> None:
    print("\n4. Смешанный синтез")
    models_ = _fake_model_files(("ru", "en"))
    engine = make_engine(
        models_,
        {"ru": ["xenia", "aidar"], "en": ["en_0", "en_1"]},
    )
    audio = engine.synthesize_mixed("synthesize использует пути движка", "xenia")
    ru_calls = engine._stubs["ru"].calls  # type: ignore[attr-defined]
    en_calls = engine._stubs["en"].calls  # type: ignore[attr-defined]
    check(len(en_calls) == 1 and len(ru_calls) == 1, "по одному запросу в каждую модель")
    check(en_calls[0]["text"] == "synthesize", f"в английскую ушло слово: {en_calls[0]['text']!r}")
    check("использует" in ru_calls[0]["text"], "в русскую ушла русская часть")
    check(len(audio) > 0, "аудио собрано")
    check(engine.loaded_languages == ["en", "ru"], "обе модели загружены")

    # Пауза между кусками: длина больше суммы кусков
    saved_gap = audio.size
    without_gap = engine.synthesize_mixed("synthesize использует", "xenia", gap_ms=0)
    check(saved_gap > without_gap.size, "между кусками вставлена пауза")

    # Один язык — одна модель, без дробления
    engine._stubs["ru"].calls.clear()  # type: ignore[attr-defined]
    engine._stubs["en"].calls.clear()  # type: ignore[attr-defined]
    engine.synthesize_mixed("Только русский текст", "xenia")
    check(len(engine._stubs["en"].calls) == 0,  # type: ignore[attr-defined]
          "чисто русский текст не идёт в английскую модель")

    # Нет английской модели — кусок читается русской, а не падает
    only_ru = make_engine(_fake_model_files(("ru",)),
                          {"ru": ["xenia"]})
    only_ru._paths["en"] = Path("/nonexistent/v3_en.pt")  # type: ignore[index]
    audio = only_ru.synthesize_mixed("synthesize использует", "xenia")
    check(len(audio) > 0 and len(only_ru._stubs["ru"].calls) >= 1,  # type: ignore[attr-defined]
          "без английской модели латиница читается русской моделью")


def test_profiles() -> None:
    print("\n5. Профили моделей")
    ru = models_module.MODELS["ru"]
    en = models_module.MODELS["en"]
    check(ru.put_accent and ru.put_yo and ru.transliterate, "русская модель: ударение, ё, транслитерация")
    check(not en.put_accent and not en.put_yo and not en.transliterate,
          "английская модель: без ударения и транслитерации")
    check(ru.default_speaker == "xenia" and en.default_speaker == "en_0", "голоса по умолчанию разные")


def test_engine_routing() -> None:
    print("\n6. Выбор модели и параметров")
    models = _fake_model_files(("ru", "en"))
    engine = make_engine(
        models,
        {"ru": ["aidar", "baya", "kseniya", "eugene", "xenia", "random"],
         "en": ["en_0", "en_1", "en_42", "random"]},
    )

    engine.synthesize("Привет, мир", "aidar")
    ru_call = engine._stubs["ru"].calls[-1]  # type: ignore[attr-defined]
    check(ru_call["speaker"] == "aidar", "русский текст ушёл в русскую модель")
    check(ru_call["put_accent"] is True and ru_call["put_yo"] is True,
          "русской модели переданы put_accent/put_yo")
    check("Hello" not in ru_call["text"], "латиница в русском тексте транслитерирована")

    engine.synthesize("Hello, world", None)
    en_call = engine._stubs["en"].calls[-1]  # type: ignore[attr-defined]
    check(en_call["speaker"] == "en_0", "английский текст ушёл в английскую модель")
    check("put_accent" not in en_call and "put_yo" not in en_call,
          "английской модели НЕ передаются put_accent/put_yo")
    check("Hello" in en_call["text"], "английский текст не транслитерируется")

    engine.synthesize("Hello again", "en_42")
    check(engine._stubs["en"].calls[-1]["speaker"] == "en_42",  # type: ignore[attr-defined]
          "голос en_42 принимается напрямую")

    engine.synthesize("Привет", "en_42")
    check(engine._stubs["ru"].calls[-1]["speaker"] == "xenia",  # type: ignore[attr-defined]
          "чужой голос заменяется голосом по умолчанию своей модели")

    engine.synthesize("Hello", {"ru": "aidar", "en": "en_1"})
    check(engine._stubs["en"].calls[-1]["speaker"] == "en_1",  # type: ignore[attr-defined]
          "словарь «язык → голос» поддерживается")

    engine.synthesize("42", "xenia", "en")
    digits = engine._stubs["en"].calls[-1]["text"]  # type: ignore[attr-defined]
    check("forty-two" in digits, f"цифры развёрнуты по-английски: {digits!r}")

    check(engine.loaded_languages == ["en", "ru"], "загружены обе модели")


def test_numbers_en() -> None:
    print("\n7. Английские числительные")
    cases = {
        "0": "zero",
        "7": "seven",
        "42": "forty-two",
        "100": "one hundred",
        "365": "three hundred sixty-five",
        "1000": "one thousand",
        "2.5": "two point five",
        "1,234": "one thousand two hundred thirty-four",
    }
    for raw, expected in cases.items():
        got = TextProcessor.digits_to_words_en(raw)
        check(got == expected, f"{raw} → {got}")


def test_config(tmpdir: Path) -> None:
    print("\n8. Конфиг: языки, голоса, миграция")
    saved_dir, saved_file = cfg_module.CONFIG_DIR, cfg_module.CONFIG_FILE
    cfg_module.CONFIG_DIR = tmpdir
    cfg_module.CONFIG_FILE = tmpdir / "config.json"
    try:
        config = ConfigManager()
        check(config.speaker_for("ru") == "xenia", "голос ru по умолчанию")
        check(config.speaker_for("en") == "en_0", "голос en по умолчанию")
        check(config.model_for("en").endswith("v3_en.pt"), "путь к английской модели известен")
        check(config.language == "auto", "язык по умолчанию — auto")

        config.speaker = "aidar"
        config.language = "ru"
        config.save()
        reloaded = ConfigManager()
        check(reloaded.speaker_for("ru") == "aidar", "голос ru сохранён")
        check(reloaded.speaker_for("en") == "en_0", "английский голос не пострадал")
        check(reloaded.language == "ru", "язык сохранён")

        # Старый конфиг: только tts.model_path и tts.speaker
        cfg_module.CONFIG_FILE.write_text(json.dumps({
            "tts": {"model_path": "/old/path/v5_ru.pt", "speaker": "baya"},
        }), encoding="utf-8")
        old = ConfigManager()
        check(old.model_for("ru") == "/old/path/v5_ru.pt",
              "старый model_path подхвачен как русская модель")
        check(old.speaker_for("ru") == "baya", "старый speaker подхвачен как русский голос")
        check(old.speaker_for("en") == "en_0", "для английского остался голос по умолчанию")
    finally:
        cfg_module.CONFIG_DIR, cfg_module.CONFIG_FILE = saved_dir, saved_file


def test_download_hint() -> None:
    print("\n9. Подсказка при отсутствии модели")
    engine = TTSEngine(sample_rate=48000,
                       models={"ru": _fake_model_files(("ru",))["ru"],
                               "en": "/nonexistent/v3_en.pt"})
    try:
        engine.load("en")
    except Exception as exc:
        message = str(exc)
        check("silero-tts-model download en" in message, "в ошибке есть команда загрузки")
        check("/nonexistent/v3_en.pt" in message, "в ошибке указан ожидаемый путь")
    else:
        raise AssertionError("загрузка несуществующей модели не упала")


def main() -> int:
    print("Многоязычность Silero TTS")
    test_detect()
    test_resolve_with_installed()
    test_split_by_script()
    test_mixed_synthesis()
    test_profiles()
    test_engine_routing()
    test_numbers_en()
    with tempfile.TemporaryDirectory() as tmp:
        test_config(Path(tmp))
    test_download_hint()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
