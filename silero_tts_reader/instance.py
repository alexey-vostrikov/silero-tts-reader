"""Работающий экземпляр: кто запущен и не устарел ли он.

GApplication сам следит, чтобы процесс был один, но второй запуск он просто
передаёт первому — и тот продолжает работать старым кодом. Здесь решается
другое: если запускаемая сборка новее работающей, работающую просим выйти и
занимаем её место.

Сведения о работающем экземпляре лежат в ``running.json`` (pid + метка
сборки). Файл — не источник истины, а подсказка: перед действием pid
проверяется, поэтому устаревший файл после падения приложения ничего не ломает.
"""
from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from .build_info import BuildInfo, is_newer

RUNNING_FILE = Path.home() / ".cache" / "silero-tts-reader" / "running.json"
# Сколько ждём корректного завершения старого экземпляра, секунды
SHUTDOWN_TIMEOUT = 8.0
POLL_INTERVAL = 0.1


@dataclass(frozen=True)
class RunningInstance:
    pid: int
    stamp: int
    version: str = ""
    started: float = 0.0

    def is_alive(self) -> bool:
        return pid_alive(self.pid)

    def as_build(self) -> BuildInfo:
        return BuildInfo(stamp=self.stamp, version=self.version)


def _process_state(pid: int) -> str:
    """Состояние процесса из /proc/<pid>/stat ('' — если прочитать нельзя).

    Поле 3: R — работает, S — спит, Z — зомби (уже завершился, но родитель
    ещё не забрал код возврата). Формат: ``pid (comm) state ...``, а в comm
    могут быть пробелы и скобки, поэтому состояние берём после последней «)».
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = stat.rsplit(")", 1)[-1].split()
    return tail[0] if tail else ""


def pid_alive(pid: int) -> bool:
    """Жив ли процесс. EPERM считаем живым: он существует, просто не наш.

    Зомби считается мёртвым: `kill(pid, 0)` на нём успешен, и ожидание
    завершения иначе не заканчивается никогда (тест это и поймал).
    """
    if pid <= 0:
        return False
    if _process_state(pid) == "Z":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_our_process(pid: int) -> bool:
    """Процесс жив и это действительно наше приложение, а не чужой pid.

    pid в файле может достаться другому процессу после перезагрузки — убивать
    его нельзя, поэтому сверяем командную строку.
    """
    if not pid_alive(pid):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", "replace"
        )
    except OSError:
        return False
    return "silero" in cmdline.lower()


def read() -> RunningInstance | None:
    """Прочитать сведения о работающем экземпляре (None, если нет или устарел)."""
    try:
        raw = RUNNING_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        instance = RunningInstance(
            pid=int(data["pid"]),
            stamp=int(data.get("stamp", 0)),
            version=str(data.get("version", "")),
            started=float(data.get("started", 0.0)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _is_our_process(instance.pid):
        return None
    return instance


def register(info: BuildInfo) -> None:
    """Записать себя как работающий экземпляр."""
    try:
        RUNNING_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNNING_FILE.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "stamp": info.stamp,
                    "version": info.version,
                    "started": time.time(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # подсказка для следующего запуска, не критично


def clear() -> None:
    """Убрать файл при выходе (только если он наш)."""
    try:
        data = json.loads(RUNNING_FILE.read_text(encoding="utf-8"))
        if int(data.get("pid", -1)) != os.getpid():
            return
    except (OSError, ValueError):
        return
    try:
        RUNNING_FILE.unlink()
    except OSError:
        pass


def request_shutdown(instance: RunningInstance, timeout: float = SHUTDOWN_TIMEOUT) -> bool:
    """Попросить экземпляр выйти и дождаться его завершения.

    Сначала SIGTERM: приложение ловит его и завершается штатно (сохраняет
    конфиг, останавливает потоки). SIGKILL — только если оно не отреагировало,
    иначе следующий запуск получит занятую шину D-Bus.
    """
    if not _is_our_process(instance.pid):
        return False

    try:
        os.kill(instance.pid, signal.SIGTERM)
    except OSError:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(instance.pid):
            return True
        time.sleep(POLL_INTERVAL)

    try:
        os.kill(instance.pid, signal.SIGKILL)
    except OSError:
        pass

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not pid_alive(instance.pid):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def replace_older(info: BuildInfo) -> RunningInstance | None:
    """Заменить работающий экземпляр, если он старше текущей сборки.

    Возвращает сведения о закрытом экземпляре либо None, если замены не было.
    Ровно та же или более новая сборка остаётся работать: повторный запуск
    просто поднимает её окно, как обычно делает GApplication.
    """
    instance = read()
    if instance is None:
        return None
    if not is_newer(info, instance.as_build()):
        return None
    if request_shutdown(instance):
        return instance
    return None
