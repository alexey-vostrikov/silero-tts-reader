"""Silero TTS engine using PyTorch package (.pt models).

Движок держит несколько моделей — по одной на язык — и грузит их по требованию.
Язык выбирается по тексту (кириллица → русская модель, латиница → английская)
или задаётся явно; у каждой модели свои требования: русским нужны
``put_accent``/``put_yo`` и кириллица, английской эти параметры передавать
нельзя, а транслитерация латиницы ей прямо вредна.
"""
from __future__ import annotations

import contextlib
import re
import threading
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .. import log
from ..i18n import _
from ..models import DEFAULT_LANG, MODELS, ModelProfile, resolve_language, split_by_script


class TTSLoadError(Exception):
    """Raised when the Silero model cannot be loaded."""


class TTSEngine:
    """Synthesizes speech from text using Silero PyTorch packaged models.

    Thread-safe: synthesize() may be called from worker threads.
    """

    _ABBR = re.compile(
        r'\b(?:г|гр|д|доц|зам|зав|им|ин|кг|кв|км|л|м|мг|мл|мм|'
        r'млн|млрд|напр|оз|пр|проф|р|руб|рис|с|см|стр|т|тел|'
        r'тыс|ул|ф|чел|эт)\.'
    )
    _SENTENCE_END = re.compile(r'(?<=[.!?…])\s+')

    def __init__(
        self,
        model_path: str | None = None,
        sample_rate: int = 48000,
        models: dict[str, str] | None = None,
        accent_mode: str = "off",
        accent_overrides: dict[str, str] | None = None,
        abbreviations: str = "off",
        abbreviations_extra: dict | None = None,
        stress_source: str = "auto",
    ) -> None:
        self._sample_rate = sample_rate
        # Ударения по словарю только для русского: словарь русский, а
        # английской модели посторонние знаки в тексте только мешают.
        self._accent_mode = accent_mode
        self._accent_overrides = accent_overrides or {}
        self._accentor = None
        self._stress_source = stress_source
        self._abbrev_mode = abbreviations
        # Правки пользователя: свои расшифровки, словочитаемые аббревиатуры
        # и список выключенных встроенных записей.
        self._abbrev_extra = abbreviations_extra or {}
        self._abbrev = None
        self._paths: dict[str, Path] = {}
        for lang, path in (models or {}).items():
            if lang in MODELS and path:
                self._paths[lang] = Path(path).expanduser()
        if model_path:
            self._paths.setdefault(DEFAULT_LANG, Path(model_path).expanduser())
        for lang in MODELS:
            self._paths.setdefault(lang, Path.home() / ".local" / "share"
                                   / "silero-tts-reader" / MODELS[lang].filename)
        self._models: dict[str, Any] = {}
        self._speakers: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, language: str | None = None) -> None:
        """Load the model of one language (русская по умолчанию).

        Остальные модели грузятся при первом обращении к их языку — держать
        в памяти 5 моделей по 55 МБ ради русского текста незачем.
        """
        with self._lock:
            self._load_model(language or DEFAULT_LANG)

    def get_available_speakers(self, language: str | None = None) -> list[str]:
        """Голоса модели языка; пустой список, если модель не установлена."""
        lang = language or DEFAULT_LANG
        with self._lock:
            if lang not in self._speakers:
                try:
                    self._load_model(lang)
                except TTSLoadError:
                    return []
            return list(self._speakers.get(lang, []))

    def resolve_language(self, text: str, requested: str | None = None) -> str:
        """Какой язык будет использован для этого текста."""
        return resolve_language(text, requested, self._is_available)

    def _is_available(self, lang: str) -> bool:
        """Есть ли модель этого языка по нашим путям (или уже загружена)."""
        if lang in self._models:
            return True
        path = self._paths.get(lang)
        return bool(path and path.is_file())

    def synthesize(
        self,
        text: str,
        speaker: str | dict[str, str] | None = None,
        language: str | None = None,
        on_prepared: Callable[[str], None] | None = None,
    ) -> np.ndarray:
        """Synthesize text → float32 numpy array at self._sample_rate Hz.

        ``speaker`` может быть именем голоса или словарём «язык → голос»:
        второй вариант нужен приложению, где голос выбран для каждого языка.

        ``on_prepared`` получает ровно ту строку, которая уйдёт в модель, — с
        расшифрованными сокращениями и расставленными ударениями. Приложению
        это нужно, чтобы показать, что именно произносится: без такой строки
        неверное ударение выглядит ошибкой словаря, хотя словарь его поставил.
        """
        # self.resolve_language, а не модульная функция: у движка свои пути
        # к моделям, и доступность языка считается по ним.
        lang = self.resolve_language(text, language)
        profile = MODELS.get(lang)

        with self._lock:
            model = self._models.get(lang)
            if model is None:
                self._load_model(lang)
                model = self._models[lang]

            speakers = self._speakers.get(lang, [])
            voice = self._pick_speaker(speaker, lang, speakers)
            prepared = self._prepare_text(text, profile)
            if lang == "ru":
                prepared = self._expand_abbreviations(prepared)
                prepared = self._apply_accents(prepared)

            log.detail(
                f"[TTSEngine] apply_tts(lang={lang}, speaker={voice!r}): {prepared!r}")
            kwargs: dict[str, Any] = {
                "text": prepared,
                "speaker": voice,
                "sample_rate": self._sample_rate,
            }
            if profile is not None and profile.put_accent:
                kwargs["put_accent"] = True
            if profile is not None and profile.put_yo:
                kwargs["put_yo"] = True

            if on_prepared is not None:
                on_prepared(prepared)

            with _no_grad():
                audio = model.apply_tts(**kwargs)
            res = audio.numpy().astype(np.float32)

        log.debug(
            f"[TTSEngine] Сгенерирован аудиомассив длины {len(res)} сэмплов "
            f"({len(res) / self._sample_rate:.2f} сек)")
        return res

    def synthesize_mixed(
        self,
        text: str,
        speaker: str | dict[str, str] | None = None,
        gap_ms: int = 80,
        default_language: str | None = None,
    ) -> np.ndarray:
        """Озвучить текст, переключая язык по письменности.

        «synthesize использует пути движка» звучит двумя голосами: латинское
        слово — английской моделью, остальное — русской. Куски одного языка
        склеиваются, между кусками вставляется короткая пауза, иначе стык двух
        моделей слышен как обрыв.

        Если модели нужного языка нет, кусок читается языком по умолчанию —
        лучше прочитать латиницу русскими фонемами, чем отказаться говорить.
        """
        default = default_language or DEFAULT_LANG
        chunks = split_by_script(text) or [(None, text)]

        pieces: list[np.ndarray] = []
        silence = np.zeros(int(self._sample_rate * max(0, gap_ms) / 1000), dtype=np.float32)
        used_languages: list[str] = []

        for lang, chunk in chunks:
            resolved = lang if lang and self._is_available(lang) else default
            segments = self.split_into_segments(chunk)
            for segment in segments:
                if not segment.strip():
                    continue
                if pieces and gap_ms:
                    pieces.append(silence)
                pieces.append(self.synthesize(segment, speaker, resolved))
                if not used_languages or used_languages[-1] != resolved:
                    used_languages.append(resolved)

        if not pieces:
            return np.zeros(0, dtype=np.float32)

        audio = np.concatenate(pieces)
        log.detail(
            f"[TTSEngine] Смешанный текст: языки по порядку {used_languages}, "
            f"{len(audio) / self._sample_rate:.2f} с")
        return audio

    def split_into_segments(self, text: str, max_chars: int = 800) -> list[str]:
        """Split text into speakable segments by sentence boundaries."""
        protected = self._ABBR.sub(lambda m: m.group().replace(".", "·"), text)
        raw_sentences = self._SENTENCE_END.split(protected)

        segments: list[str] = []
        current = ""

        for sent in raw_sentences:
            sent = sent.replace("·", ".").strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > max_chars and current:
                segments.append(current.strip())
                current = sent
            else:
                current = (current + " " + sent).strip() if current else sent

        if current:
            segments.append(current.strip())

        return segments or [text.strip()]

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return bool(self._models)

    @property
    def loaded_languages(self) -> list[str]:
        with self._lock:
            return sorted(self._models)

    @property
    def model_path(self) -> str:
        return str(self._paths.get(DEFAULT_LANG, ""))

    def reload_abbreviations(self, extra: dict | None = None) -> None:
        """Перечитать правки сокращений после редактора.

        Раскрытие строится один раз и держит свод правил в памяти. Без сброса
        добавленная аббревиатура подействовала бы только после перезапуска —
        ровно та же ошибка, что была с правками ударений.
        """
        if extra is not None:
            self._abbrev_extra = extra
        self._abbrev = None

    def reload_accents(self, overrides: dict[str, str] | None = None) -> None:
        """Перечитать правки ударений после окна настроек.

        Акцентор живёт весь сеанс и кэширует свод правок: без сброса
        сохранённая в настройках правка осталась бы на бумаге до перезапуска.
        Пересоздавать акцентор нельзя — вместе с ним из файла заново
        развернулся бы словарь на 1,5 млн словоформ, а он не изменился.
        """
        if overrides is not None:
            self._accent_overrides = overrides
        accentor = self._accentor
        if accentor is not None:
            accentor.reload_fixes(self._accent_overrides)

    # ── Внутреннее ────────────────────────────────────────────────────────────

    def _load_model(self, lang: str) -> None:
        """Загрузить модель языка. Вызывается под self._lock."""
        if lang not in MODELS:
            raise TTSLoadError(
                _("неизвестный язык модели: {lang} (есть: {known})").format(
                    lang=lang, known=", ".join(MODELS))
            )

        # Сначала проверяем файл: сообщение «модель не найдена» должно быть
        # одинаковым и с torch, и без него. Иначе без torch пользователь
        # вместо команды загрузки видит «No module named 'torch'».
        path = self._paths.get(lang) or Path(MODELS[lang].filename)
        if not path.is_file():
            raise TTSLoadError(
                _("Модель не найдена: {path}\n\n"
                  "Скачайте её:\n"
                  "  silero-tts-model download {lang}\n\n"
                  "или вручную:\n"
                  "  mkdir -p {parent}\n"
                  "  wget -O {path} {url}").format(
                    path=path, lang=lang, parent=path.parent, url=MODELS[lang].url)
            )

        import torch

        try:
            # Silero распространяет модели как torch.package-архивы
            importer = torch.package.PackageImporter(str(path))
            # Предупреждение «TypedStorage is deprecated» выдаёт сам torch:
            # в package_importer.py он оборачивает legacy-хранилища в
            # TypedStorage и там же сообщает, что уберёт это в будущем. Наш
            # код к этому не относится, перепаковать чужую модель нельзя, а в
            # консоли пользователя строка выглядит как поломка приложения.
            # Отключаем ровно её и только на время распаковки модели.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="TypedStorage is deprecated",
                    category=UserWarning,
                )
                model = importer.load_pickle("tts_models", "model")
            # TTSModelMultiAcc_v3 — не nn.Module, .eval() звать нельзя
            speakers = (
                list(model.speakers)
                if hasattr(model, "speakers")
                else list(model.get_speakers())
                if hasattr(model, "get_speakers")
                else []
            )
        except TTSLoadError:
            raise
        except Exception as exc:
            raise TTSLoadError(
                _("Ошибка загрузки модели {file}: {error}").format(file=path.name, error=exc)
            ) from exc

        self._models[lang] = model
        self._speakers[lang] = speakers

    @staticmethod
    def _pick_speaker(
        speaker: str | dict[str, str] | None,
        lang: str,
        speakers: list[str],
    ) -> str:
        if isinstance(speaker, dict):
            speaker = speaker.get(lang)
        if speaker and speaker in speakers:
            return speaker

        profile = MODELS.get(lang)
        if profile is not None and profile.default_speaker in speakers:
            return profile.default_speaker
        for candidate in speakers:
            if candidate != "random":
                return candidate
        return speakers[0] if speakers else (speaker or "")

    def _expand_abbreviations(self, text: str) -> str:
        """Развернуть сокращения до ударений: ударения ставятся по словам."""
        if self._abbrev_mode == "off" or not text:
            return text
        if self._abbrev is None:
            from .abbrev import Abbreviations

            self._abbrev = Abbreviations(
                generic=self._abbrev_mode == "all",
                extra=self._abbrev_extra)
        return self._abbrev.expand(text)

    def _apply_accents(self, text: str) -> str:
        """Разметить ударения словарём. Модель уважает поставленный знак."""
        if self._accent_mode == "off" or not text:
            return text
        if self._accentor is None:
            from .accent import Accentor

            self._accentor = Accentor(
                mode=self._accent_mode,
                overrides=self._accent_overrides,
                source=self._stress_source,
            )
        # Многозначные слова видны в журнале: у них пометки нет, и решает
        # акцентор модели. Без этой строки неверное ударение омографа
        # выглядит как ошибка словаря.
        ambiguous = self._accentor.ambiguous_words(text)
        if ambiguous:
            log.detail(
                f"[TTSEngine] ударение по смыслу, словарь не трогает: "
                f"{', '.join(sorted(set(ambiguous)))}")
        return self._accentor.accent_text(text)

    @staticmethod
    def _prepare_text(text: str, profile: ModelProfile | None) -> str:
        """Привести текст к тому, что понимает модель этого языка."""
        from .text_processor import TextProcessor

        if profile is not None and profile.transliterate:
            # Русская модель: цифры — словами, латиницу — кириллицей,
            # иначе v5 их просто не прочитает.
            if re.search(r"\d", text):
                text = TextProcessor.convert_numbers_to_words(text)
            if TextProcessor.has_non_cyrillic(text):
                text = TextProcessor.transliterate_offline(text)
            return text

        # Нерусские модели читают латиницу сами; цифры им тоже нужно развернуть,
        # но русские слова-числительные тут не годятся.
        return TextProcessor.digits_to_words_en(text)


@contextlib.contextmanager
def _no_grad():
    """torch.no_grad(), если torch есть; иначе пустой контекст (в тестах)."""
    try:
        import torch
    except ImportError:
        yield
        return
    with torch.no_grad():
        yield
