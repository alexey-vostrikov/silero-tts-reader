"""Реестр моделей Silero: какие языки есть, где лежат файлы, как их скачать.

Модуль намеренно не импортирует torch: его используют и загрузчик моделей
(``silero-tts-model``), которому torch не нужен вовсе.

У каждой модели свой набор голосов и свои требования к тексту: русские модели
(v4/v5) умеют ставить ударение и «ё» и требуют кириллицу, английская v3 —
наоборот, латиницу, а параметры ``put_accent``/``put_yo`` ей передавать нельзя
(``apply_tts`` падает). Поэтому профиль языка лежит рядом с адресом модели.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MODEL_DIR = Path.home() / ".local" / "share" / "silero-tts-reader"
BASE_URL = "https://models.silero.ai/models/tts"


@dataclass(frozen=True)
class ModelProfile:
    """Что нужно знать про модель, кроме пути к файлу."""

    lang: str
    filename: str
    url: str
    size_mb: int
    # русские модели: ударение и «ё», латиницу транслитерируем в кириллицу
    put_accent: bool = False
    put_yo: bool = False
    transliterate: bool = False
    # голоса по умолчанию, если пользователь свой не выбрал
    default_speaker: str = ""


MODELS: dict[str, ModelProfile] = {
    "ru": ModelProfile(
        lang="ru",
        filename="v5_ru.pt",
        url=f"{BASE_URL}/ru/v5_ru.pt",
        size_mb=139,
        put_accent=True,
        put_yo=True,
        transliterate=True,
        default_speaker="xenia",
    ),
    "en": ModelProfile(
        lang="en",
        filename="v3_en.pt",
        url=f"{BASE_URL}/en/v3_en.pt",
        size_mb=55,
        default_speaker="en_0",
    ),
    "de": ModelProfile(
        lang="de", filename="v3_de.pt", url=f"{BASE_URL}/de/v3_de.pt", size_mb=55,
        default_speaker="de_0",
    ),
    "es": ModelProfile(
        lang="es", filename="v3_es.pt", url=f"{BASE_URL}/es/v3_es.pt", size_mb=55,
        default_speaker="es_0",
    ),
    "fr": ModelProfile(
        lang="fr", filename="v3_fr.pt", url=f"{BASE_URL}/fr/v3_fr.pt", size_mb=55,
        default_speaker="fr_0",
    ),
    "ua": ModelProfile(
        lang="ua", filename="v3_ua.pt", url=f"{BASE_URL}/ua/v3_ua.pt", size_mb=55,
        default_speaker="ua_0",
    ),
}

# Модель, которую ставим и грузим по умолчанию.
DEFAULT_LANG = "ru"
# Латиница без кириллицы — это английский, а не «неизвестный язык»: остальные
# языки однозначно не определить, поэтому им нужен явный --lang.
_LATIN_LANGS = ("en",)

_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def model_path(lang: str) -> Path:
    """Путь к файлу модели языка (файл может отсутствовать)."""
    profile = MODELS.get(lang)
    if profile is None:
        raise KeyError(f"неизвестный язык модели: {lang}")
    return MODEL_DIR / profile.filename


def installed_languages() -> list[str]:
    return [lang for lang in MODELS if model_path(lang).is_file()]


def is_installed(lang: str) -> bool:
    return lang in MODELS and model_path(lang).is_file()


def detect_language(text: str) -> str | None:
    """Язык по письменности: кириллица → ru, латиница → en, иначе None.

    Смешанный текст (русский с латинскими вставками) считается русским: такие
    вставки транслитерируются, иначе модель их просто не прочтёт. Если нужен
    разбор по кускам, а не один язык на весь текст, — split_by_script().
    """
    if _CYRILLIC_RE.search(text):
        return "ru"
    if _LATIN_RE.search(text):
        return _LATIN_LANGS[0]
    return None


# Слово целиком: кириллица, латиница или «прочее» (цифры, пунктуация, пробелы).
_TOKEN_RE = re.compile(r"[А-Яа-яЁё]+|[A-Za-z]+|[^А-Яа-яЁёA-Za-z]+")


def split_by_script(text: str) -> list[tuple[str | None, str]]:
    """Разбить текст на куски по письменности: [(язык, кусок), …].

    Нужно, чтобы одна фраза могла звучать двумя голосами: «synthesize» —
    английским, «использует пути движка» — русским. Куски одного языка
    склеиваются, поэтому подряд идущие английские слова остаются одной фразой
    с нормальной интонацией.

    Служебные символы (пробелы, цифры, пунктуация) прилипают к предыдущему
    куску — иначе дробление съело бы пробелы и знаки препинания. Язык None
    означает «букв нет»: такой кусок читается языком по умолчанию.
    """
    chunks: list[list] = []
    for match in _TOKEN_RE.finditer(text):
        part = match.group(0)
        if _CYRILLIC_RE.search(part):
            lang: str | None = "ru"
        elif _LATIN_RE.search(part):
            lang = _LATIN_LANGS[0]
        else:
            lang = None

        if lang is None:
            if chunks:
                chunks[-1][1] += part
            else:
                chunks.append([None, part])   # текст начинается с цифр/пунктуации
            continue

        if chunks and chunks[-1][0] == lang:
            chunks[-1][1] += part
        elif chunks and chunks[-1][0] is None:
            # ведущий «нейтральный» кусок присоединяем к первому языку
            chunks[-1][0] = lang
            chunks[-1][1] += part
        else:
            chunks.append([lang, part])

    if not chunks:
        return []

    # Куски, где нет ни одной буквы (только цифры и знаки), читает язык по
    # умолчанию: цифры у русского движка разворачиваются русскими словами.
    result: list[tuple[str | None, str]] = [
        (lang, chunk.strip()) for lang, chunk in chunks
    ]
    return [(lang, chunk) for lang, chunk in result if chunk]


def resolve_language(
    text: str,
    requested: str | None = None,
    available=None,
) -> str:
    """Итоговый язык: явный запрос, если модель есть, иначе по тексту.

    ``requested`` приходит из ``--lang``/``$LANGUAGE`` и может быть кодом с
    регионом (``ru_RU``, ``en-GB``) или пустым. ``available`` — проверка
    доступности модели; движок передаёт свою, потому что у него пути к моделям
    берутся из конфига и могут отличаться от каталога по умолчанию.
    """
    is_available = available or is_installed
    if requested:
        code = requested.split("_")[0].split("-")[0].strip().lower()
        if code in MODELS and is_available(code):
            return code
    detected = detect_language(text)
    if detected and is_available(detected):
        return detected
    return DEFAULT_LANG


def download(lang: str, progress: bool = True) -> Path:
    """Скачать модель языка в MODEL_DIR. Возвращает путь к файлу."""
    profile = MODELS[lang]
    target = model_path(lang)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _hook(blocks: int, block_size: int, total: int) -> None:
        if not progress or not total:
            return
        done = min(blocks * block_size, total)
        percent = done * 100 // total
        print(f"\r  {profile.filename}: {percent:3d}%  ({done // 1048576} из {total // 1048576} МБ)",
              end="", file=sys.stderr, flush=True)

    urllib.request.urlretrieve(profile.url, target, _hook)
    if progress:
        print(file=sys.stderr)
    return target


def _cmd_list() -> int:
    print(f"каталог моделей: {MODEL_DIR}")
    print(f"{'язык':6} {'файл':12} {'размер':>8}  состояние")
    for lang, profile in MODELS.items():
        path = model_path(lang)
        state = "установлена" if path.is_file() else f"скачать: silero-tts-model download {lang}"
        print(f"{lang:6} {profile.filename:12} {profile.size_mb:5d} МБ  {state}")
    return 0


def _cmd_download(langs: list[str]) -> int:
    if not langs:
        return _cmd_list()
    failed = 0
    for lang in langs:
        if lang not in MODELS:
            print(f"неизвестный язык: {lang} (есть: {', '.join(MODELS)})", file=sys.stderr)
            failed += 1
            continue
        if is_installed(lang):
            print(f"  {lang}: уже установлена ({model_path(lang)})")
            continue
        print(f"  {lang}: качаю {MODELS[lang].url}")
        try:
            path = download(lang)
        except OSError as exc:
            print(f"  {lang}: не удалось скачать — {exc}", file=sys.stderr)
            failed += 1
            continue
        print(f"  {lang}: готово ({path}, {path.stat().st_size // 1048576} МБ)")
    return 1 if failed else 0


def _cmd_path(lang: str) -> int:
    if lang not in MODELS:
        print(f"неизвестный язык: {lang}", file=sys.stderr)
        return 1
    path = model_path(lang)
    print(path)
    return 0 if path.is_file() else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="silero-tts-model",
        description="Модели Silero TTS: что установлено, что скачать.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="список моделей и их состояние")
    download_parser = sub.add_parser("download", help="скачать модели")
    download_parser.add_argument("languages", nargs="*", metavar="ЯЗЫК",
                                 help=f"коды языков: {', '.join(MODELS)}")
    path_parser = sub.add_parser("path", help="путь к файлу модели")
    path_parser.add_argument("language", metavar="ЯЗЫК")

    args = parser.parse_args(argv)
    if args.command == "download":
        return _cmd_download(args.languages)
    if args.command == "path":
        return _cmd_path(args.language)
    return _cmd_list()


if __name__ == "__main__":
    sys.exit(main())
