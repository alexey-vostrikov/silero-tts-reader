"""Метка сборки: когда и из какого кода собран этот экземпляр.

Нужна, чтобы при запуске новой версии понять, что уже работающий экземпляр
старше, и заменить его. Версия пакета для этого не годится: при правке кода
без смены тега она одинаковая, а сборки — разные.

Источники, по убыванию точности:

1. ``build-info.json`` рядом с кодом — его пишет ``packaging/build-deb.sh`` при
   сборке пакета (время сборки и коммит);
2. время коммита git — когда приложение запущено прямо из репозитория;
3. время изменения ``silero_tts_reader/__init__.py`` — последний резерв, чтобы
   сравнение вообще было возможным.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .i18n import _

PACKAGE_DIR = Path(__file__).resolve().parent

BUILD_INFO_CANDIDATES = (
    PACKAGE_DIR.parent / "build-info.json",   # /usr/lib/silero-tts/build-info.json
    PACKAGE_DIR / "build-info.json",
)


@dataclass(frozen=True)
class BuildInfo:
    """Метка сборки: время (epoch, сек), версия и коммит."""

    stamp: int
    version: str = "unknown"
    commit: str = ""

    @property
    def is_known(self) -> bool:
        return self.stamp > 0

    def describe(self) -> str:
        from datetime import datetime

        if not self.is_known:
            return self.version
        moment = datetime.fromtimestamp(self.stamp).strftime("%Y-%m-%d %H:%M:%S")
        suffix = f" ({self.commit})" if self.commit else ""
        return _("{version} от {moment}{suffix}").format(
            version=self.version, moment=moment, suffix=suffix
        )


def _from_file() -> BuildInfo | None:
    for path in BUILD_INFO_CANDIDATES:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            return BuildInfo(
                stamp=int(data.get("build_time", 0)),
                version=str(data.get("version", "unknown")),
                commit=str(data.get("commit", "")),
            )
        except (TypeError, ValueError):
            continue
    return None


def _from_git() -> BuildInfo | None:
    root = PACKAGE_DIR.parent
    if not (root / ".git").exists():
        return None
    try:
        stamp = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h %s"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return BuildInfo(stamp=int(stamp), version="git", commit=commit)
    except ValueError:
        return None


def _from_mtime() -> BuildInfo:
    try:
        stamp = int((PACKAGE_DIR / "__init__.py").stat().st_mtime)
    except OSError:
        stamp = 0
    return BuildInfo(stamp=stamp, version="unknown")


def current() -> BuildInfo:
    """Метка текущей сборки (никогда не падает)."""
    return _from_file() or _from_git() or _from_mtime()


def is_newer(one: BuildInfo, other: BuildInfo) -> bool:
    """Строго новее: равные метки не считаются поводом для замены."""
    return one.is_known and other.is_known and one.stamp > other.stamp
