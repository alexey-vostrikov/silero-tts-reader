"""Проверки окружения для пакетов, которых нет в apt.

Правило, вокруг которого построен набор: приложение не ставит ничего системно
и не начинает скачивать без явной команды. Всё, чего нет в репозитории Ubuntu,
живёт в отдельном окружении; apt-пакеты в нём видны, но не дублируются.

Настоящие пакеты здесь не ставятся: окружение подменяется временным каталогом,
а pip — заглушкой. Проверяется логика, а не сеть.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.core import extras
from silero_tts_reader.run_options import (
    INSTALL_EXTRAS_FLAG,
    LIST_EXTRAS_FLAG,
    parse_extras_command,
)

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ✓ {message}")
    else:
        raise AssertionError(message)


def fake_venv(root: Path, packages: dict[str, bool] | None = None) -> Path:
    """Создать вид окружения: site-packages и метаданные пакетов.

    ``packages`` — какие пакеты «установлены»: имя → есть ли.
    """
    wanted = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = root / "lib" / wanted / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for name, present in (packages or {}).items():
        if not present:
            continue
        info = site / f"{name.replace('-', '_')}-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Name: {name}\nVersion: 1.0\n", encoding="utf-8")
    return site


def test_commands() -> None:
    """Ключи окружения разбираются и не пересекаются с отладочными."""
    print("\n1. Разбор ключей окружения")
    argv = ["silero-tts-reader"]
    check(parse_extras_command(argv) is None, "без ключей команды нет")
    check(parse_extras_command([*argv, LIST_EXTRAS_FLAG]) == (LIST_EXTRAS_FLAG, []),
          "--list-extras распознан")
    check(parse_extras_command([*argv, INSTALL_EXTRAS_FLAG]) == (INSTALL_EXTRAS_FLAG, []),
          "--install-extras без имён — умолчание")
    check(parse_extras_command([*argv, INSTALL_EXTRAS_FLAG, "stress", "ruaccent"])
          == (INSTALL_EXTRAS_FLAG, ["stress", "ruaccent"]),
          "имена после ключа собраны")
    # Ключи отладки не должны попадать в список пакетов.
    check(parse_extras_command(
        [*argv, INSTALL_EXTRAS_FLAG, "stress", "--debug"])
        == (INSTALL_EXTRAS_FLAG, ["stress"]),
        "следующий ключ прекращает список")
    check(parse_extras_command([*argv, "--max-time", "60"]) is None,
          "отладочные ключи не принимаются за команду")


def test_paths() -> None:
    """Окружение живёт в каталоге данных, а не рядом с приложением."""
    print("\n2. Где лежит окружение")
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = tmp
        try:
            expected = Path(tmp) / "silero-tts-reader" / "venv"
            check(extras.venv_dir() == expected,
                  f"путь от XDG_DATA_HOME: {extras.venv_dir()}")
            check(extras.site_packages() is None, "нет окружения — нет пакетов")

            fake_venv(expected, {"silero-stress": True})
            check(extras.site_packages() == expected / "lib"
                  / f"python{sys.version_info.major}.{sys.version_info.minor}"
                  / "site-packages",
                  "site-packages найден")
            check(extras.is_installed(extras.EXTRAS["stress"]),
                  "silero-stress виден по метаданным")
            check(not extras.is_installed(extras.EXTRAS["ruaccent"]),
                  "ruaccent не виден, раз его нет")
            check(extras.installed_keys() == ["stress"],
                  f"установлено: {extras.installed_keys()}")
        finally:
            if old is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old


def test_foreign_python() -> None:
    """Окружение чужой версии Python не подмешивается молча."""
    print("\n3. Окружение другой версии Python")
    with tempfile.TemporaryDirectory() as tmp:
        other = Path(tmp) / "venv"
        (other / "lib" / "python3.99" / "site-packages").mkdir(parents=True)
        check(extras.site_packages(other) is None,
              "site-packages другой версии не принят")
        check(extras.activate(other) is False,
              "и пути не подмешаны")


def test_install_failures() -> None:
    """Ошибки установки громкие: молча продолжать нельзя."""
    print("\n4. Отказы установки")
    try:
        extras.install(["нет-такого"])
    except ValueError as exc:
        check("неизвестный пакет" in str(exc), f"опечатка в ключе: {exc}")
    else:
        raise AssertionError("опечатка в ключе прошла молча")

    # pip вернул ошибку — код возврата должен дойти до вызывающего.
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = tmp
        try:
            fake_venv(extras.venv_dir(), {})
            original = subprocess.run

            def fake_pip(command, **kwargs):
                # Окружение не создаём заново: оно уже «есть».
                if "venv" in command:
                    return original(["true"], **kwargs)
                return subprocess.CompletedProcess(command, 3)

            subprocess.run = fake_pip        # type: ignore[assignment]
            try:
                code = extras.install(["stress"], echo=lambda *_: None)
            finally:
                subprocess.run = original    # type: ignore[assignment]
            check(code == 3, f"код pip возвращён: {code}")
        finally:
            if old is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old


def test_no_network_at_start() -> None:
    """Проверка наличия пакетов не ходит в сеть и не создаёт окружение."""
    print("\n5. Запуск не трогает сеть")
    source = (ROOT / "silero_tts_reader" / "core" / "extras.py").read_text(
        encoding="utf-8")
    # Установка — единственное место с pip; вызов только из явной команды.
    check("pip\", \"install" in source or '"install"' in source,
          "установка вызывается через pip")
    caller = (ROOT / "silero_tts_reader" / "__main__.py").read_text(encoding="utf-8")
    check("parse_extras_command" in caller,
          "команда окружения разбирается в точке входа")
    # Никакого автоматического вызова install при старте приложения.
    app = (ROOT / "silero_tts_reader" / "app.py").read_text(encoding="utf-8")
    check("extras.install" not in app,
          "приложение не ставит пакеты само")


def main() -> int:
    test_commands()
    test_paths()
    test_foreign_python()
    test_install_failures()
    test_no_network_at_start()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
