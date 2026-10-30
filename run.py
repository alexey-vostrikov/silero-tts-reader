#!/usr/bin/env python3
"""Launcher — auto-activates .venv if present, then starts the application."""
import contextlib
import os
import sys
from pathlib import Path


def _ensure_layer_shell_preload() -> None:
    """Перезапустить процесс с LD_PRELOAD, чтобы заработал layer-shell.

    Библиотека ``libgtk4-layer-shell`` перехватывает создание поверхности
    Wayland; при загрузке через ctypes перехват не встаёт, и протокол молча не
    работает (окно остаётся там, куда его положит композитор). Перезапуск
    делает только Wayland-сессия и только один раз.
    """
    if not os.environ.get("WAYLAND_DISPLAY"):
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
    """If a .venv exists next to this script, prepend its site-packages to sys.path."""
    script_dir = Path(__file__).parent.resolve()
    venv_dir = script_dir / ".venv"

    if not venv_dir.exists():
        return

    if sys.prefix == str(venv_dir):
        return

    lib_dir = venv_dir / "lib"
    if not lib_dir.exists():
        return

    site_packages = sorted(lib_dir.glob("python*/site-packages"))
    if not site_packages:
        return

    for sp in site_packages:
        sp_str = str(sp)
        if sp_str not in sys.path:
            sys.path.insert(0, sp_str)

    os.environ.setdefault("VIRTUAL_ENV", str(venv_dir))


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


_activate_venv()
_suppress_noise()

sys.path.insert(0, str(Path(__file__).parent.resolve()))

from silero_tts_reader.__main__ import main  # noqa: E402

_ensure_layer_shell_preload()

if __name__ == "__main__":
    main()
