#!/usr/bin/env python3
"""Замена устаревшего запущенного экземпляра новым.

Проверяется главное: сборка новее → старый процесс завершается штатно и место
занимает новый; сборка та же или старше → ничего не закрывается.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader import build_info, instance

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def with_running_file(tmpdir: Path):
    """Подменить файл работающего экземпляра на временный."""
    saved = instance.RUNNING_FILE
    instance.RUNNING_FILE = tmpdir / "running.json"
    return saved


def spawn_sleeper(marker: str) -> subprocess.Popen:
    """Процесс, похожий на наше приложение: в cmdline есть «silero»."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", marker, "silero"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def write_running(pid: int, stamp: int, version: str = "old") -> None:
    instance.RUNNING_FILE.parent.mkdir(parents=True, exist_ok=True)
    instance.RUNNING_FILE.write_text(
        json.dumps({"pid": pid, "stamp": stamp, "version": version, "started": time.time()}),
        encoding="utf-8",
    )


def test_build_info() -> None:
    print("\n1. Метка сборки")
    info = build_info.current()
    check(info.is_known, f"метка определена: {info.describe()}")
    check(info.stamp > 1_000_000_000, f"похоже на epoch-секунды: {info.stamp}")

    older = build_info.BuildInfo(stamp=info.stamp - 3600, version="old")
    newer = build_info.BuildInfo(stamp=info.stamp + 3600, version="new")
    check(build_info.is_newer(newer, info), "сборка на час новее считается новее")
    check(not build_info.is_newer(older, info), "сборка на час старше — не новее")
    check(not build_info.is_newer(info, info), "равные метки не повод для замены")
    check(not build_info.is_newer(build_info.BuildInfo(stamp=0), info),
          "неизвестная метка не считается новее")


def test_no_instance(tmpdir: Path) -> None:
    print("\n2. Ничего не запущено")
    saved = with_running_file(tmpdir)
    try:
        check(instance.read() is None, "файла нет — экземпляр не найден")
        replaced = instance.replace_older(build_info.BuildInfo(stamp=int(time.time()), version="new"))
        check(replaced is None, "заменять некого — None")
    finally:
        instance.RUNNING_FILE = saved


def test_replace_older(tmpdir: Path) -> None:
    print("\n3. Запущена старая сборка")
    saved = with_running_file(tmpdir)
    sleeper = spawn_sleeper("old-instance")
    try:
        write_running(sleeper.pid, stamp=int(time.time()) - 7200, version="0.0.9")
        running = instance.read()
        check(running is not None and running.pid == sleeper.pid, "старый экземпляр найден")

        replaced = instance.replace_older(
            build_info.BuildInfo(stamp=int(time.time()), version="0.1.0")
        )
        check(replaced is not None, "старый экземпляр закрыт")
        sleeper.wait(timeout=10)
        check(sleeper.poll() is not None, "процесс старого экземпляра завершился")
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
        instance.RUNNING_FILE = saved


def test_keep_newer(tmpdir: Path) -> None:
    print("\n4. Запущена та же или более новая сборка")
    saved = with_running_file(tmpdir)
    sleeper = spawn_sleeper("new-instance")
    try:
        write_running(sleeper.pid, stamp=int(time.time()) + 3600, version="0.2.0")
        replaced = instance.replace_older(
            build_info.BuildInfo(stamp=int(time.time()), version="0.1.0")
        )
        check(replaced is None, "более новый экземпляр не закрывается")
        check(sleeper.poll() is None, "его процесс продолжает работать")

        write_running(sleeper.pid, stamp=build_info.current().stamp, version="same")
        check(instance.replace_older(build_info.current()) is None,
              "та же сборка не закрывается (обычный второй запуск)")
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
        instance.RUNNING_FILE = saved


def test_foreign_pid(tmpdir: Path) -> None:
    print("\n5. Чужой pid в файле")
    saved = with_running_file(tmpdir)
    sleeper = spawn_sleeper("not-our-app")
    # тот же процесс, но без «silero» в командной строке
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "unrelated"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        write_running(other.pid, stamp=1, version="ancient")
        check(instance.read() is None, "чужой процесс не считается нашим экземпляром")
        check(instance.replace_older(build_info.current()) is None,
              "чужой процесс не убивается")
        check(other.poll() is None, "чужой процесс жив")
    finally:
        other.kill()
        if sleeper.poll() is None:
            sleeper.kill()
        instance.RUNNING_FILE = saved


def test_register_clear(tmpdir: Path) -> None:
    print("\n6. Регистрация и снятие отметки")
    saved = with_running_file(tmpdir)
    try:
        info = build_info.current()
        instance.register(info)
        data = json.loads(instance.RUNNING_FILE.read_text(encoding="utf-8"))
        check(data["pid"] == os.getpid(), "записан свой pid")
        check(data["stamp"] == info.stamp, "записана метка сборки")

        # файл от другого pid снимать нельзя: его пишет работающий экземпляр
        instance.RUNNING_FILE.write_text(
            json.dumps({"pid": os.getpid() + 1, "stamp": 1}), encoding="utf-8"
        )
        instance.clear()
        check(instance.RUNNING_FILE.exists(), "чужую отметку не удаляем")

        instance.register(info)
        instance.clear()
        check(not instance.RUNNING_FILE.exists(), "свою отметку снимаем при выходе")
    finally:
        instance.RUNNING_FILE = saved


def main() -> int:
    print("Замена устаревшего экземпляра")
    test_build_info()
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        test_no_instance(directory)
        test_replace_older(directory)
        test_keep_newer(directory)
        test_foreign_pid(directory)
        test_register_clear(directory)
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
