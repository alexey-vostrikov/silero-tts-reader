"""Позиционирование окна: wlr-layer-shell (GTK4) и запасной путь для X11.

В GTK4 у окна больше нет ``move()``: место выбирает композитор, и программно
поставить окно в угол нельзя ни под Wayland, ни под X11. Отсюда три пути:

* **Wayland с поддержкой протокола** — ``libgtk4-layer-shell``: окно объявляет,
  к каким краям его прижать, и получает слой поверх обычных окон. Привязок для
  Python нет, поэтому библиотека вызывается через ctypes.
* **X11** — поверхность X11 отдаёт свой ``xid``, а запрос ``configure`` двигает
  окно как раньше. Работает и под настоящим X11, и под XWayland.
* **Wayland без протокола** (GNOME Shell без патча) — ничего не делаем: окно
  встанет туда, куда его положит композитор, обычно по центру.

ВАЖНО про ``LD_PRELOAD``. Библиотека перехватывает создание ``wl_surface``, а
при загрузке через ctypes это происходит уже после ``libwayland-client`` —
перехват не встаёт. Симптом тихий: ``is_supported()`` и ``is_layer_window()``
отвечают «да», якоря проставлены, а окно стоит по центру. Поэтому ``run.py``
перезапускает процесс с этой переменной (см. ``ensure_preloaded()``).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk  # noqa: E402

# Порядок значений задаёт протокол; в заголовках gtk4-layer-shell он такой же.
EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOTTOM = 0, 1, 2, 3
LAYER_BOTTOM, LAYER_TOP, LAYER_OVERLAY, LAYER_BACKGROUND = 0, 1, 2, 3
KEYBOARD_MODE_NONE, KEYBOARD_MODE_EXCLUSIVE, KEYBOARD_MODE_ON_DEMAND = 0, 1, 2

_lib = None
_loaded = False


def library_path() -> str:
    """Путь к библиотеке протокола (пустая строка, если её нет)."""
    name = ctypes.util.find_library("gtk4-layer-shell") or ""
    if name and os.path.exists(name):
        return name
    for folder in ("/usr/lib/x86_64-linux-gnu", "/usr/lib", "/usr/lib64"):
        candidate = os.path.join(folder, "libgtk4-layer-shell.so.0")
        if os.path.exists(candidate):
            return candidate
    return ""


def ensure_preloaded() -> None:
    """Перезапустить процесс с LD_PRELOAD, если библиотека ещё не загружена.

    Без этого перехват создания поверхности не встаёт, и протокол молча не
    работает. Перезапуск делается один раз: признак — переменная окружения,
    которую выставляет сам же ``os.execve``.
    """
    path = library_path()
    if not path or os.environ.get("SILERO_LAYER_SHELL_PRELOADED") == path:
        return
    # Только для Wayland: под X11 библиотека бесполезна, а preload лишний.
    if not os.environ.get("WAYLAND_DISPLAY"):
        return

    env = dict(os.environ)
    previous = env.get("LD_PRELOAD", "")
    env["LD_PRELOAD"] = f"{path} {previous}".strip()
    env["SILERO_LAYER_SHELL_PRELOADED"] = path
    try:
        os.execve(sys.executable, [sys.executable, *sys.argv], env)
    except OSError:
        # Перезапуск не удался — работаем как есть, без протокола.
        return


def _load():
    """Загрузить библиотеку протокола и объявить прототипы. None, если её нет."""
    global _lib, _loaded
    if _loaded:
        return _lib
    _loaded = True

    path = library_path()
    if not path:
        _lib = None
        return None
    try:
        lib = ctypes.CDLL(path)
    except OSError:
        _lib = None
        return None

    lib.gtk_layer_is_supported.restype = ctypes.c_int
    lib.gtk_layer_is_supported.argtypes = []
    for func, argtypes in (
        ("gtk_layer_init_for_window", [ctypes.c_void_p]),
        ("gtk_layer_set_anchor", [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
        ("gtk_layer_set_margin", [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
        ("gtk_layer_set_layer", [ctypes.c_void_p, ctypes.c_int]),
        ("gtk_layer_set_keyboard_mode", [ctypes.c_void_p, ctypes.c_int]),
        ("gtk_layer_set_namespace", [ctypes.c_void_p, ctypes.c_char_p]),
        ("gtk_layer_set_monitor", [ctypes.c_void_p, ctypes.c_void_p]),
    ):
        fn = getattr(lib, func, None)
        if fn is None:
            _lib = None
            return None
        fn.argtypes = argtypes
        fn.restype = None

    _lib = lib
    return lib


def available() -> bool:
    """Протокол поддерживается этим композитором?"""
    lib = _load()
    if lib is None:
        return False
    try:
        return bool(lib.gtk_layer_is_supported())
    except Exception:
        return False


def reason() -> str:
    """Почему недоступно — для журнала, чтобы не гадать."""
    lib = _load()
    if lib is None:
        return "нет библиотеки libgtk4-layer-shell"
    try:
        if lib.gtk_layer_is_supported():
            return "поддерживается"
        return "композитор не реализует wlr-layer-shell"
    except Exception as exc:
        return f"ошибка проверки: {exc}"


def monitor_at(window, index: int):
    """N-й монитор (GdkMonitor). Нужен, когда в сессии несколько выходов."""
    display = window.get_display() if window is not None else None
    display = display or Gdk.Display.get_default()
    if display is None:
        return None
    # В GDK4 мониторы приходят списком-моделью.
    monitors = display.get_monitors()
    if index < 0 or index >= monitors.get_n_items():
        return None
    return monitors.get_item(index)


def primary_monitor(window):
    """Первый монитор: в GDK4 понятия «основной» больше нет."""
    return monitor_at(window, 0)


def apply_corner(gtk_window, margin: int = 16, namespace: bytes = b"silero-tts-reader",
                 keyboard: int = KEYBOARD_MODE_ON_DEMAND,
                 monitor_index: int | None = None) -> bool:
    """Прижать окно к правому нижнему углу через layer-shell.

    Принимает GtkWindow и вызывается ДО показа окна: протокол переоформляет
    поверхность при её создании, для уже показанного окна это поздно.
    """
    lib = _load()
    if lib is None or not lib.gtk_layer_is_supported():
        return False

    # PyGObject хранит за объектом указатель на C-структуру; hash() для GObject
    # возвращает именно его. Нужен GtkWindow, а не GdkSurface: функция ждёт
    # GtkWindow*, иначе она «настраивает» чужую память.
    handle = ctypes.c_void_p(hash(gtk_window))
    try:
        lib.gtk_layer_init_for_window(handle)
        lib.gtk_layer_set_namespace(handle, namespace)
        lib.gtk_layer_set_anchor(handle, EDGE_BOTTOM, 1)
        lib.gtk_layer_set_anchor(handle, EDGE_RIGHT, 1)
        lib.gtk_layer_set_margin(handle, EDGE_BOTTOM, int(margin))
        lib.gtk_layer_set_margin(handle, EDGE_RIGHT, int(margin))
        lib.gtk_layer_set_layer(handle, LAYER_OVERLAY)
        lib.gtk_layer_set_keyboard_mode(handle, keyboard)
        if monitor_index is not None:
            monitor = monitor_at(gtk_window, monitor_index)
            if monitor is not None:
                lib.gtk_layer_set_monitor(handle, ctypes.c_void_p(hash(monitor)))
    except Exception:
        return False
    return True


def move_x11(window, x: int, y: int) -> bool:
    """Поставить окно в точку (x, y) под X11.

    Своего move() у окна в GTK4 нет, поэтому двигаем запросом к X-серверу по
    xid поверхности. Подключение своё: ``get_xdisplay()`` в PyGObject отдаёт
    непрозрачную обёртку без методов.
    """
    surface = window.get_surface()
    if surface is None:
        return False
    try:
        # нужен только на X11: проверяем, что поверхность — X11
        from gi.repository import GdkX11
    except (ImportError, ValueError):
        return False
    if not isinstance(surface, GdkX11.X11Surface):
        return False

    try:
        from Xlib import display as xlib_display
        connection = xlib_display.Display()
        connection.create_resource_object(
            "window", surface.get_xid()).configure(x=int(x), y=int(y))
        connection.sync()
        connection.close()
    except Exception:
        return False
    return True


def place_bottom_right(window, width: int, height: int, margin: int = 16,
                       monitor_index: int | None = None) -> str:
    """Поставить окно в правый нижний угол доступным способом.

    Возвращает ``"layer"``, ``"x11"`` или ``"none"`` — по этому значению
    вызывающий понимает, надо ли ещё что-то делать с позицией.
    """
    if apply_corner(window, margin=margin, monitor_index=monitor_index):
        return "layer"

    monitor = (monitor_at(window, monitor_index) if monitor_index is not None
               else primary_monitor(window))
    if monitor is None:
        return "none"
    area = monitor.get_geometry()
    x = area.x + area.width - width - margin
    y = area.y + area.height - height - margin
    if move_x11(window, x, y):
        return "x11"
    return "none"
