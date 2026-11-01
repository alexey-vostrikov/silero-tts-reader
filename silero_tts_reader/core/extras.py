"""Дополнительные Python-пакеты, которых нет в apt.

Приложение не ставит ничего системно. Всё, что уже есть в репозитории Ubuntu —
torch, numpy, PyGObject, pynput, sounddevice, python-xlib, — берётся из apt и
обновляется вместе с системой. Всё, чего в apt нет, складывается в отдельное
окружение::

    ~/.local/share/silero-tts-reader/venv

Окружение создаётся с ``--system-site-packages``, поэтому apt-пакеты в нём
видны, а внутрь попадают только отсутствующие. Так установка не требует root
(в том числе для пользователя без sudo) и не спорит с пакетным менеджером:
системный Python остаётся нетронутым.

Почему не просто ``pip install --user``: Debian и Ubuntu помечают системное
окружение как externally managed, и pip отказывается туда писать без
``--break-system-packages``. Отдельное окружение — честный способ, а не
подавление защиты.

Запуск установки только явный: ``silero-tts-reader --install-extras``. В начале
работы приложение ничего не скачивает — сеть в момент запуска была бы
неожиданностью, а на чужой машине ещё и поводом для отказа.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Extra:
    """Один дополнительный пакет.

    ``spec`` — то, что передаётся pip: имя и, при нужде, ограничение версии.
    Диапазон, а не точная версия: пакет без API-обещаний ломается редко, а
    прибитая версия мешает обновлениям безопасности.
    """

    key: str
    spec: str
    module: str
    title: str
    purpose: str
    size_mb: int


EXTRAS: dict[str, Extra] = {
    "stress": Extra(
        key="stress",
        spec="silero-stress",
        module="silero_stress",
        title="Silero Stress",
        purpose=(
            "ударения и омографы по контексту: 4 млн словоформ, 2,2 тыс. "
            "омографов. Нужен только torch, который уже стоит из apt"
        ),
        size_mb=65,
    ),
    "ruaccent": Extra(
        key="ruaccent",
        spec="ruaccent",
        module="ruaccent",
        title="ruaccent",
        purpose=(
            "альтернативный акцентор: тянет onnxruntime и transformers, "
            "модели занимают ещё около 160 МБ"
        ),
        size_mb=280,
    ),
    "rate": Extra(
        key="rate",
        spec="pyrubberband",
        module="pyrubberband",
        title="pyrubberband",
        purpose=(
            "изменение скорости с сохранением тона; без системной "
            "librubberband кнопка скорости остаётся выключенной"
        ),
        size_mb=1,
    ),
}

DEFAULT_KEYS = ("stress",)


def venv_dir() -> Path:
    """Каталог окружения. Тот же корень, что у моделей и данных словаря."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "silero-tts-reader" / "venv"


def site_packages(directory: Path | None = None) -> Path | None:
    """Каталог site-packages окружения для текущей версии Python.

    Версия сверяется, а не берётся первая попавшаяся: окружение, созданное
    другим Python, для этого интерпретатора бесполезно, и подмешивать его
    пути молча нельзя.
    """
    root = Path(directory) if directory else venv_dir()
    lib = root / "lib"
    if not lib.is_dir():
        return None
    wanted = f"python{sys.version_info.major}.{sys.version_info.minor}"
    exact = lib / wanted / "site-packages"
    if exact.is_dir():
        return exact
    return None


def activate(directory: Path | None = None) -> bool:
    """Подмешать пакеты окружения к текущему процессу.

    Именно подмешивание путей, а не перезапуск интерпретатора окружения:
    PyGObject и GTK стоят системно, а перезапуск внутрь venv требует, чтобы
    там был виден gi — с ``--system-site-packages`` он виден, но лишний exec
    ломает уже открытые файлы, сокеты и переменные окружения сессии.
    """
    packages = site_packages(directory)
    if packages is None:
        return False
    path = str(packages)
    if path in sys.path:
        return True
    sys.path.insert(0, path)
    os.environ.setdefault("VIRTUAL_ENV", str(Path(directory) if directory
                                            else venv_dir()))
    return True


def is_installed(extra: Extra) -> bool:
    """Установлен ли пакет — по каталогу dist-info в окружении.

    Проверка по метаданным, а не по импорту: импорт тянет torch и модели
    (секунды и сотни мегабайт), а спросить «стоит ли» нужно мгновенно.
    """
    packages = site_packages()
    if packages is None:
        return False
    normalized = extra.spec.split(">")[0].split("<")[0].split("=")[0].strip()
    normalized = normalized.replace("-", "_").lower()
    for entry in packages.iterdir():
        name = entry.name.lower()
        if name.startswith(f"{normalized}-") and name.endswith(".dist-info"):
            return True
    return False


def installed_keys() -> list[str]:
    """Ключи уже установленных пакетов — для справки и настроек."""
    return [key for key, extra in EXTRAS.items() if is_installed(extra)]


def missing_keys(keys: tuple[str, ...] | list[str] | None = None) -> list[str]:
    wanted = keys if keys is not None else tuple(EXTRAS)
    return [key for key in wanted
            if key in EXTRAS and not is_installed(EXTRAS[key])]


def ensure_venv(echo=print) -> Path:
    """Создать окружение, если его нет. Сеть здесь не нужна.

    ``--system-site-packages`` обязателен: иначе окружение не увидит ни
    системный torch, ни PyGObject, и внутрь пришлось бы ставить всё заново.
    """
    root = venv_dir()
    if site_packages(root) is not None:
        return root
    # i18n: нет перевода — журнал установки дополнений в консоли
    echo(f"  создаю окружение: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(root)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else "без объяснения"
        raise RuntimeError(
            # i18n: нет перевода — журнал установки дополнений в консоли
            f"окружение не создано: {reason}. "
            f"Проверьте, что стоит python3-venv: sudo apt install python3-venv")
    if site_packages(root) is None:
        raise RuntimeError(
            # i18n: нет перевода — журнал установки дополнений в консоли
            f"окружение создано, но не видно для python{sys.version_info.major}."
            f"{sys.version_info.minor}: {root}")
    return root


def install(keys: tuple[str, ...] | list[str] | None = None,
            echo=print) -> int:
    """Поставить пакеты в окружение. Возвращает код возврата pip.

    Fail-fast: если pip не смог, код возврата ненулевой и вызывающий обязан
    этим воспользоваться. Продолжать работу «как будто поставилось» нельзя —
    пользователь выбрал источник ударений и вправе знать, что его нет.
    """
    wanted = list(keys) if keys is not None else list(DEFAULT_KEYS)
    unknown = [key for key in wanted if key not in EXTRAS]
    if unknown:
        # i18n: нет перевода — журнал установки дополнений в консоли
        raise ValueError(f"неизвестный пакет: {', '.join(unknown)}")
    if not wanted:
        return 0

    root = ensure_venv(echo=echo)
    python = root / "bin" / "python"
    if not python.is_file():
        # i18n: нет перевода — журнал установки дополнений в консоли
        raise RuntimeError(f"в окружении нет интерпретатора: {python}")

    specs = [EXTRAS[key].spec for key in wanted]
    total = sum(EXTRAS[key].size_mb for key in wanted)
    # i18n: нет перевода — журнал установки дополнений в консоли
    echo(f"  ставлю в окружение: {', '.join(specs)} (~{total} МБ)")
    # Ограничений версий нет намеренно: подбирать их вручную значит
    # поддерживать список самому. pip сам разрешит зависимости от torch,
    # который виден из apt через --system-site-packages.
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", *specs],
        check=False,
    )
    if result.returncode != 0:
        # i18n: нет перевода — журнал установки дополнений в консоли
        echo(f"  ✗ pip вернул {result.returncode}")
        return result.returncode

    activate(root)
    for key in wanted:
        extra = EXTRAS[key]
        mark = "✓" if is_installed(extra) else "✗"
        echo(f"  {mark} {extra.title}: {'установлен' if mark == '✓' else 'не виден'}")
    return 0


def describe(echo=print) -> None:
    """Что доступно из apt, что в окружении, что ещё нет."""
    # i18n: нет перевода — журнал установки дополнений в консоли
    echo(f"  окружение: {venv_dir()}")
    packages = site_packages()
    # i18n: нет перевода — журнал установки дополнений в консоли
    echo(f"  пригодно для python{sys.version_info.major}."
         f"{sys.version_info.minor}: {'да' if packages else 'нет'}")
    if packages:
        # i18n: нет перевода — журнал установки дополнений в консоли
        echo(f"  пакеты: {packages}")
    echo()
    echo("  Дополнительные пакеты (в apt их нет, ставятся в окружение):")
    for key, extra in EXTRAS.items():
        state = "установлен" if is_installed(extra) else "нет"
        # i18n: нет перевода — журнал установки дополнений в консоли
        echo(f"    {key:9} {extra.title:12} {state:11} ~{extra.size_mb} МБ")
        echo(f"              {extra.purpose}")
    echo()
    echo("  Установка: silero-tts-reader --install-extras [ключ…]")
    # i18n: нет перевода — журнал установки дополнений в консоли
    echo(f"  Без ключей ставится: {', '.join(DEFAULT_KEYS)}")
