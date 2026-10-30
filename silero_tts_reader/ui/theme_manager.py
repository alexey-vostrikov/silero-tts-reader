"""Менеджер темы — системная тёмная/светлая тема и акцентный цвет (GTK4/GNOME).

Версия для PyGObject: вместо ``pyqtSignal`` — сигналы ``GObject``, вместо
``QDBusConnection`` — ``Gio.DBusProxy``, вместо ``QColor`` — строка ``#rrggbb``.
Все методы возвращают цвета строками, чтобы их можно было подставлять в CSS GTK.

GTK здесь не используется вообще (только Gio/GLib/GObject), поэтому версия
Gtk не запрашивается: ``gi.require_version`` упал бы, если процесс уже загрузил
другую версию (это и случилось при переходе на GTK4).
"""
from __future__ import annotations

import subprocess
from typing import ClassVar

from gi.repository import Gio, GLib, GObject

DEFAULT_ACCENT = "#6366f1"

GNOME_ACCENT_MAP = {
    "blue": "#3584e4",
    "teal": "#129eaf",
    "green": "#2ec27e",
    "yellow": "#e5a50a",
    "orange": "#e66100",
    "red": "#e01b24",
    "pink": "#d56199",
    "purple": "#9141ac",
    "slate": "#62a0ea",
}

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.Settings"

_INTERFACE_SCHEMA = "org.gnome.desktop.interface"


# ── Работа с цветом ──────────────────────────────────────────────────────────


def _parse_hex(color: str) -> tuple[int, int, int]:
    """``#rrggbb`` (или ``#rgb``) → кортеж r, g, b. Некорректная строка — акцент."""
    value = (color or "").strip()
    if value.startswith("#"):
        value = value[1:]
    try:
        if len(value) == 3:
            return tuple(int(c * 2, 16) for c in value)  # type: ignore[return-value]
        if len(value) >= 6:
            return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        pass
    return _parse_hex(DEFAULT_ACCENT)


def _to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def shift_color(color: str, factor: float) -> str:
    """Осветлить (``factor > 1``) или затемнить (``factor < 1``) цвет.

    Замена ``QColor.lighter()/darker()``: Qt работал в HSV, здесь — линейное
    масштабирование каналов с ограничением; для акцентов разница не видна,
    зато не нужен QColor.
    """
    r, g, b = _parse_hex(color)
    return _to_hex((r * factor, g * factor, b * factor))


def _luminance(color: str) -> float:
    """Относительная яркость 0..255 — выбор «светлее/темнее» при наведении."""
    r, g, b = _parse_hex(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def rgba(color: str, alpha: float) -> str:
    """``#rrggbb`` + прозрачность → строка ``rgba(r, g, b, a)`` для CSS GTK."""
    r, g, b = _parse_hex(color)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


# ── Чтение системных настроек ────────────────────────────────────────────────


def _interface_settings() -> Gio.Settings | None:
    """``Gio.Settings`` для схемы GNOME либо ``None``, если схемы в системе нет.

    Важно: ``Gio.Settings.new()`` на отсутствующей схеме не бросает исключение, а
    роняет процесс с ``GLib-GIO-ERROR **: Settings schema ... is not installed``
    (это abort, try/except не спасает). Поэтому сначала смотрим схему в источнике,
    и только потом создаём объект.
    """
    try:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return None
        schema = source.lookup(_INTERFACE_SCHEMA, True)
        if schema is None:
            return None
        return Gio.Settings.new_full(schema, None, None)
    except Exception:
        return None


def _has_key(settings: Gio.Settings, key: str) -> bool:
    try:
        schema = settings.props.settings_schema
        return bool(schema and schema.has_key(key))
    except Exception:
        return False


def _portal_read(key: str):
    """Прочитать ``org.freedesktop.appearance.<key>`` через портал. ``None`` — нет ответа."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            PORTAL_BUS,
            PORTAL_PATH,
            PORTAL_IFACE,
            None,
        )
        reply = proxy.call_sync(
            "Read",
            GLib.Variant("(ss)", ("org.freedesktop.appearance", key)),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        if reply is None:
            return None
        value = reply.unpack()[0]
        # Портал отдаёт вложенный вариант; при необходимости разворачиваем его.
        if isinstance(value, GLib.Variant):
            return value.unpack()
        return value
    except Exception:
        return None


def _gsettings_get(key: str) -> str:
    """Запасной путь через CLI: портал может быть не поднят, gsettings есть всегда."""
    try:
        res = subprocess.run(
            ["gsettings", "get", _INTERFACE_SCHEMA, key],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if res.returncode == 0:
            return res.stdout.strip().strip("'\"").lower()
    except Exception:
        pass
    return ""


class ThemeManager(GObject.GObject):
    """Разрешение темы (система/тёмная/светлая), акцентный цвет и уведомления об изменении."""

    # ClassVar: это описание сигналов GObject, а не значение экземпляра.
    __gsignals__: ClassVar[dict[str, tuple]] = {
        # RUN_FIRST, тип возврата None, аргументы (is_dark: bool)
        "theme-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "accent-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__()
        self._mode = "system"  # system | dark | light
        self._use_system_accent = True
        self._last_detected_dark = True
        self._last_detected_accent = DEFAULT_ACCENT
        self._subscriptions: list[int] = []

        self._subscribe_signals()

        # Портал поднимается не сразу после логина — пробуем несколько раз, как в Qt-версии.
        for delay in (500, 1500, 3000, 6000, 10000):
            GLib.timeout_add(delay, self._sync_once)

        self._last_detected_dark = self.is_dark()
        self._last_detected_accent = self.accent_color()

    # ── Подписки на изменения ────────────────────────────────────────────────

    def _subscribe_signals(self) -> None:
        settings = _interface_settings()
        if settings is not None:
            for key in ("color-scheme", "accent-color", "gtk-theme"):
                if _has_key(settings, key):
                    handler = settings.connect(f"changed::{key}", self._on_settings_changed)
                    self._subscriptions.append(handler)

        # Портал: org.freedesktop.portal.Settings.SettingChanged
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.signal_subscribe(
                PORTAL_BUS,
                PORTAL_IFACE,
                "SettingChanged",
                PORTAL_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_portal_setting_changed,
            )
        except Exception:
            pass

    def _on_settings_changed(self, *_args) -> None:
        self._sync_once()

    def _on_portal_setting_changed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        try:
            namespace = params.unpack()[0]
        except Exception:
            namespace = ""
        if namespace == "org.freedesktop.appearance":
            self._sync_once()

    def _sync_once(self) -> bool:
        """Сверить текущее состояние с последним известным и известить подписчиков."""
        cur_dark = self.is_dark()
        cur_accent = self.accent_color()

        dark_changed = cur_dark != self._last_detected_dark
        accent_changed = cur_accent != self._last_detected_accent

        self._last_detected_dark = cur_dark
        self._last_detected_accent = cur_accent

        if dark_changed:
            self.emit("theme-changed", cur_dark)
        if accent_changed:
            self.emit("accent-changed")
        return False  # одноразовый таймер GLib

    # ── Режим и акцент ───────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        if mode in ("system", "dark", "light"):
            self._mode = mode
            self._sync_once()

    def get_mode(self) -> str:
        return self._mode

    @property
    def use_system_accent(self) -> bool:
        return self._use_system_accent

    @use_system_accent.setter
    def use_system_accent(self, enabled: bool) -> None:
        self._use_system_accent = bool(enabled)
        self._sync_once()

    def accent_color(self) -> str:
        if not self._use_system_accent:
            return DEFAULT_ACCENT
        return self.detect_system_accent_color()

    def accent_hover_color(self) -> str:
        base = self.accent_color()
        return shift_color(base, 0.87) if _luminance(base) > 140 else shift_color(base, 1.2)

    def accent_pressed_color(self) -> str:
        base = self.accent_color()
        return shift_color(base, 0.75) if _luminance(base) > 140 else shift_color(base, 1.35)

    def is_dark(self) -> bool:
        if self._mode == "dark":
            return True
        if self._mode == "light":
            return False
        return self.detect_system_is_dark()

    # ── Определение системных значений ───────────────────────────────────────

    @staticmethod
    def detect_system_is_dark() -> bool:
        # 1. Портал Freedesktop: 1 — тёмная, 2 — светлая.
        value = _portal_read("color-scheme")
        if value == 1:
            return True
        if value == 2:
            return False

        # 2. GSettings (GNOME 42+): "prefer-dark" / "prefer-light" / "default".
        settings = _interface_settings()
        scheme = ""
        if settings is not None and _has_key(settings, "color-scheme"):
            try:
                scheme = settings.get_string("color-scheme")
            except Exception:
                scheme = ""
        if not scheme:
            scheme = _gsettings_get("color-scheme")
        if "dark" in scheme:
            return True
        if "light" in scheme:
            return False

        # 3. Имя темы GTK: тёмные темы содержат dark.
        theme = ""
        if settings is not None and _has_key(settings, "gtk-theme"):
            try:
                theme = settings.get_string("gtk-theme")
            except Exception:
                theme = ""
        if not theme:
            theme = _gsettings_get("gtk-theme")
        if theme:
            return "dark" in theme.lower()

        return True  # как и раньше: по умолчанию тёмная

    @staticmethod
    def detect_system_accent_color() -> str:
        # 1. Портал: accent-color приходит как (r, g, b) в долях 0..1.
        value = _portal_read("accent-color")
        if isinstance(value, (tuple, list)) and len(value) == 3:
            try:
                r, g, b = (float(c) for c in value)
                return _to_hex((r * 255, g * 255, b * 255))
            except Exception:
                pass

        # 2. GNOME 47+: org.gnome.desktop.interface accent-color (имя или #rrggbb).
        settings = _interface_settings()
        raw = ""
        if settings is not None and _has_key(settings, "accent-color"):
            try:
                raw = settings.get_string("accent-color")
            except Exception:
                raw = ""
        if not raw:
            raw = _gsettings_get("accent-color")
        raw = raw.strip().lower()
        if raw in GNOME_ACCENT_MAP:
            return GNOME_ACCENT_MAP[raw]
        if raw.startswith("#") and len(raw) in (7, 9):
            return raw[:7]

        return DEFAULT_ACCENT
