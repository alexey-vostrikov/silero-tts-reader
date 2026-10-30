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
from pathlib import Path
from typing import Any

import numpy as np

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
    ) -> None:
        self._sample_rate = sample_rate
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
    ) -> np.ndarray:
        """Synthesize text → float32 numpy array at self._sample_rate Hz.

        ``speaker`` может быть именем голоса или словарём «язык → голос»:
        второй вариант нужен приложению, где голос выбран для каждого языка.
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

            print(f"[TTSEngine] apply_tts(lang={lang}, speaker={voice!r}): {prepared!r}",
                  flush=True)
            kwargs: dict[str, Any] = {
                "text": prepared,
                "speaker": voice,
                "sample_rate": self._sample_rate,
            }
            if profile is not None and profile.put_accent:
                kwargs["put_accent"] = True
            if profile is not None and profile.put_yo:
                kwargs["put_yo"] = True

            with _no_grad():
                audio = model.apply_tts(**kwargs)
            res = audio.numpy().astype(np.float32)

        print(f"[TTSEngine] Сгенерирован аудиомассив длины {len(res)} сэмплов "
              f"({len(res) / self._sample_rate:.2f} сек)", flush=True)
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
        print(f"[TTSEngine] Смешанный текст: языки по порядку {used_languages}, "
              f"{len(audio) / self._sample_rate:.2f} с", flush=True)
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

        import torch  # noqa: PLC0415 — без torch движок не нужен вовсе

        try:
            # Silero распространяет модели как torch.package-архивы
            importer = torch.package.PackageImporter(str(path))
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

    @staticmethod
    def _prepare_text(text: str, profile: ModelProfile | None) -> str:
        """Привести текст к тому, что понимает модель этого языка."""
        from .text_processor import TextProcessor  # noqa: PLC0415 — тяжёлый импорт

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
        import torch  # noqa: PLC0415
    except ImportError:
        yield
        return
    with torch.no_grad():
        yield
