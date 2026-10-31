#!/usr/bin/env python3
"""Launcher — auto-activates .venv if present, then starts the application."""
import contextlib
import os
import sys
from pathlib import Path


def _positioning_enabled() -> bool:
    """Включено ли глобальное позиционирование в настройках приложения.

    Читается до импорта приложения и без него: нужен только один ключ. Если
    файла нет, ключа нет или он битый — режим считается выключенным, как и по
    умолчанию: без layer-shell окно встаёт туда, куда его положит композитор.
    """
    import json

    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) \
        / "silero-tts-reader" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    value = (data.get("ui") or {}).get("window_positioning", "off")
    return value in ("auto", "layer")


def _ensure_layer_shell_preload() -> None:
    """Перезапустить процесс с LD_PRELOAD, чтобы заработал layer-shell.

    Библиотека ``libgtk4-layer-shell`` перехватывает создание поверхности
    Wayland; при загрузке через ctypes перехват не встаёт, и протокол молча не
    работает (окно остаётся там, куда его положит композитор). Перезапуск
    делает только Wayland-сессия и только один раз.

    Перезапуск нужен, лишь когда позиционирование включено и выбран способ с
    layer-shell: при выключенном режиме протокол не используется вовсе, и
    лишний LD_PRELOAD в процессе ни к чему.
    """
    if not os.environ.get("WAYLAND_DISPLAY"):
        return
    if not _positioning_enabled():
        return
    marker = "SILERO_LAYER_SHELL_PRELOADED"
    if os.environ.get(marker):
        return
    for folder in ("/usr/lib/x86_64-linux-gnu", "/usr/lib", "/usr/lib64"):
        library = os.path.join(folder, "libgtk4-layer-shell.so.0")
        if os.path.exists(library):
            break
    else:
        return

    env = dict(os.environ)
    env["LD_PRELOAD"] = f"{library} {env.get('LD_PRELOAD', '')}".strip()
    env[marker] = library
    try:
        os.execve(sys.executable, [sys.executable, *sys.argv], env)
    except OSError:
        return  # без перезапуска работаем как есть, без протокола


def _activate_venv() -> None:
    """Подмешать пакеты окружений к текущему процессу.

    Источников два, и оба необязательны:

    * ``.venv`` рядом со скриптом — окружение из ``install.sh``, когда
      приложение запущено из исходников;
    * ``~/.local/share/silero-tts-reader/venv`` — окружение, куда приложение
      ставит то, чего нет в apt (см. ``core/extras.py``). Оно нужно и для
      установленного из deb варианта, где каталог приложения принадлежит root.

    Порядок важен: локальное окружение из исходников ближе к разработке и
    подмешивается последним, то есть оказывается первым в ``sys.path``.
    """
    script_dir = Path(__file__).parent.resolve()
    candidates = [extras_venv_dir(), script_dir / ".venv"]

    for venv in candidates:
        if venv is None or not venv.exists() or sys.prefix == str(venv):
            continue
        packages = _site_packages_for(venv)
        if packages is None:
            continue
        path = str(packages)
        if path not in sys.path:
            sys.path.insert(0, path)
        os.environ.setdefault("VIRTUAL_ENV", str(venv))


def extras_venv_dir() -> Path | None:
    """Окружение приложения для пакетов, которых нет в apt.

    Считается здесь, а не импортом из ``core.extras``: launcher подмешивает
    пути ДО того, как пакет приложения станет импортируемым, — иначе
    дополнительные пакеты не были бы видны самому же модулю extras.
    """
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "silero-tts-reader" / "venv"


def _site_packages_for(venv: Path) -> Path | None:
    """Каталог site-packages окружения именно для этого интерпретатора."""
    wanted = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    packages = wanted / "site-packages"
    return packages if packages.is_dir() else None


def _suppress_noise() -> None:
    """Suppress known harmless warnings."""
    # pynput Xlib warning when XAUTHORITY is not set
    os.environ.setdefault("PYNPUT_BACKEND", "xorg")
    # Point to default Xauthority location if not set
    xauth = os.path.expanduser("~/.Xauthority")
    if os.path.exists(xauth):
        os.environ.setdefault("XAUTHORITY", xauth)

    import logging
    logging.getLogger("Xlib").setLevel(logging.ERROR)

    # Redirect stderr temporarily to swallow Xlib xauth warnings
    # that come from native C code (can't be caught by Python logging)
    import io

    class _XlibFilter(io.TextIOWrapper):
        def write(self, s: str) -> int:
            if "Xlib.xauth" in s or "xauthority" in s.lower():
                return len(s)
            return super().write(s)

    # Only wrap if stderr is a real TTY (don't break pipes/redirects)
    if hasattr(sys.stderr, "buffer"):
        with contextlib.suppress(Exception):
            sys.stderr = _XlibFilter(sys.stderr.buffer, line_buffering=True)


sys.path.insert(0, str(Path(__file__).parent.resolve()))
_activate_venv()
_suppress_noise()

from silero_tts_reader.__main__ import main  # noqa: E402

_ensure_layer_shell_preload()

if __name__ == "__main__":
    main()
