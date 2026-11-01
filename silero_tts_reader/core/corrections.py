"""Правки к словарю ударений: чтение, проверка и запись ``corrections.json``.

Зачем отдельный слой поверх ``accent.py``. Файл правок пишется целиком, и одна
негодная запись делает негодным весь файл: ``load_corrections`` на разборе
вернёт пустой словарь, приложение продолжит работать без единой правки и не
скажет об этом. Поэтому проверка пары «слово — ударённая форма» стоит здесь,
до записи на диск, а окно настроек только показывает то, что вернула проверка.

Слоёв два, как и в самом приложении:

* ``corrections.json`` — поставляемые правки (в дереве исходников файл свой,
  в установленном пакете принадлежит root и только читается);
* ``stress.json`` в каталоге настроек — правки пользователя, они сильнее.

Правка ложится в тот слой, откуда пришло слово: так один и тот же словарь не
расползается на две копии, а в установленном виде правка не теряется при
обновлении пакета.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Приватные имена accent.py намеренно: проверка обязана быть ровно той же,
# что и при загрузке словаря, иначе окно настроек примет то, что потом
# молча отбросит load_corrections.
from .accent import (
    _WORD_RE,
    STRESS_MARK,
    _decode_stress,
    has_valid_stress,
)

LAYER_BUILTIN = "builtin"
LAYER_USER = "user"

# Причины отказа — код, а не текст: сообщение собирает окно настроек, где есть
# перевод, а этот модуль остаётся без i18n.
EMPTY_WORD = "empty-word"
EMPTY_FORM = "empty-form"
WORD_HAS_MARK = "word-has-mark"
NOT_A_WORD = "not-a-word"
NO_MARK = "no-mark"
MARK_POSITION = "mark-position"
LETTERS_DIFFER = "letters-differ"
READ_ONLY = "read-only"
BROKEN_FILE = "broken-file"


class CorrectionError(ValueError):
    """Правку нельзя записать. ``reason`` — код из констант выше."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Correction:
    """Строка свода: слово, ударённая форма и слой, откуда она взялась."""

    word: str
    form: str
    layer: str


def normalize(word: str, form: str) -> tuple[str, str]:
    """Привести пару к виду файла и проверить её. ``CorrectionError`` при отказе.

    Знак «+» ставится ПЕРЕД ударной гласной — только так его понимает Silero.
    Кроме положения знака проверяется состав: буквы формы обязаны совпадать со
    словом. «звон+ит» при слове «звонишь» — это другая словоформа, и такая
    правка подменила бы в тексте одно слово другим.
    """
    word = word.strip().lower()
    form = form.strip()
    if not word:
        raise CorrectionError(EMPTY_WORD)
    if not form:
        raise CorrectionError(EMPTY_FORM)
    if STRESS_MARK in word:
        raise CorrectionError(WORD_HAS_MARK)
    if not _WORD_RE.fullmatch(word):
        raise CorrectionError(NOT_A_WORD)
    if STRESS_MARK not in form:
        raise CorrectionError(NO_MARK)
    if not has_valid_stress(form):
        raise CorrectionError(MARK_POSITION)
    if _decode_stress(form).lower() != word:
        raise CorrectionError(LETTERS_DIFFER)
    return word, form


def load_file(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Разобрать файл: служебные ключи (``_comment``) и сами правки.

    Порядок записей сохраняется: файл правят руками и глазами, и перестановка
    строк при каждом сохранении превращала бы одну правку в diff на весь файл.
    """
    if not path.is_file():
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorrectionError("broken-json") from exc
    if not isinstance(raw, dict):
        raise CorrectionError("not-an-object")
    meta: dict[str, str] = {}
    entries: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.startswith("_"):
            meta[key] = value
        else:
            entries[key] = value
    return meta, entries


def save_file(path: Path, meta: dict[str, str], entries: dict[str, str]) -> None:
    """Записать файл целиком: временный файл рядом, затем замена.

    Запись поверх на месте опасна: оборванная запись оставляет обрезанный JSON,
    и все правки пропадают разом. ``os.replace`` в пределах одного каталога
    атомарен, поэтому читатель видит либо старый файл, либо новый.
    """
    data: dict[str, object] = dict(meta)
    data.update(entries)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                    suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_file():
            # Права исходного файла сохраняются: иначе правка, сделанная от
            # своего имени, оставила бы файл с правами 0600 из mkstemp.
            os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


class Corrections:
    """Свод правок из двух файлов и запись в тот, которому слово принадлежит."""

    def __init__(self, builtin: Path, user: Path) -> None:
        self.builtin_path = Path(builtin)
        self.user_path = Path(user)
        self._builtin_meta: dict[str, str] = {}
        self._user_meta: dict[str, str] = {}
        self._builtin: dict[str, str] = {}
        self._user: dict[str, str] = {}
        self._broken: Path | None = None
        self.reload()

    def _read(self, path: Path) -> tuple[dict[str, str], dict[str, str]]:
        """Разобрать файл, запомнив испорченный вместо падения.

        Окно настроек — то место, где испорченный файл и чинят, поэтому оно
        обязано открыться. Падение здесь означало бы, что единственный
        инструмент правки недоступен из-за ошибки в правимом файле.
        """
        try:
            return load_file(path)
        except CorrectionError:
            self._broken = path
            return {}, {}

    def reload(self) -> None:
        """Перечитать оба файла с диска."""
        self._broken = None
        self._builtin_meta, self._builtin = self._read(self.builtin_path)
        self._user_meta, self._user = self._read(self.user_path)

    @property
    def broken(self) -> Path | None:
        """Файл, который не разобрался: правки в него не пишутся."""
        return self._broken

    def _guard(self) -> None:
        """Отказ писать, пока файл не разобран: иначе правки пропадут."""
        if self._broken is not None:
            raise CorrectionError(BROKEN_FILE)

    @property
    def writable(self) -> bool:
        """Можно ли править поставляемый файл: в пакете он принадлежит root."""
        return (self.builtin_path.is_file() and os.access(self.builtin_path, os.W_OK)
                and self.builtin_path != self._broken)

    def rows(self) -> list[Correction]:
        """Все правки по алфавиту; слово из stress.json перекрывает встроенное."""
        words = sorted(set(self._builtin) | set(self._user))
        return [
            Correction(word, self._user.get(word) or self._builtin[word],
                       LAYER_USER if word in self._user else LAYER_BUILTIN)
            for word in words
        ]

    def path_for(self, layer: str) -> Path:
        return self.user_path if layer == LAYER_USER else self.builtin_path

    def layer_of(self, word: str) -> str | None:
        """Откуда берётся слово сейчас: слоя нет — ``None``."""
        if word in self._user:
            return LAYER_USER
        if word in self._builtin:
            return LAYER_BUILTIN
        return None

    def save(self, word: str, form: str) -> Correction:
        """Записать правку. Возвращает строку с тем слоем, куда она легла."""
        self._guard()
        word, form = normalize(word, form)
        layer = LAYER_USER if word in self._user else LAYER_BUILTIN
        if layer == LAYER_BUILTIN and not self.writable:
            # Файл поставляется пакетом: правка идёт в свои, а не пропадает
            # молча при следующем обновлении.
            layer = LAYER_USER
        if layer == LAYER_USER:
            self._user[word] = form
            save_file(self.user_path, self._user_meta, self._user)
        else:
            self._builtin[word] = form
            save_file(self.builtin_path, self._builtin_meta, self._builtin)
        return Correction(word, form, layer)

    def remove(self, word: str) -> str:
        """Убрать правку. Возвращает слой, из которого она удалена."""
        self._guard()
        word = word.strip().lower()
        if word in self._user:
            del self._user[word]
            save_file(self.user_path, self._user_meta, self._user)
            return LAYER_USER
        if word in self._builtin:
            if not self.writable:
                raise CorrectionError(READ_ONLY)
            del self._builtin[word]
            save_file(self.builtin_path, self._builtin_meta, self._builtin)
            return LAYER_BUILTIN
        raise KeyError(word)

    def can_remove(self, word: str) -> bool:
        """Можно ли удалить строку: встроенную в пакете — нельзя."""
        if self._broken is not None:
            return False
        word = word.strip().lower()
        if word in self._user:
            return True
        return word in self._builtin and self.writable
