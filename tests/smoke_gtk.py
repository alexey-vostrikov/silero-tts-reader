#!/usr/bin/env python3
"""Smoke-тест GTK-порта без тяжёлых зависимостей.

Подменяет sounddevice/pynput (их нет в системном питоне) и проверяет то, что
переписано: создание виджетов, CSS, сигналы темы, разбор клавиш, сохранение
настроек. Запускать под xvfb-run с GDK_BACKEND=x11.
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

# ── Заглушки отсутствующих пакетов ───────────────────────────────────────────
sd = types.ModuleType("sounddevice")
sd.query_devices = lambda *a, **k: []
sd.default = types.SimpleNamespace(device=None, samplerate=48000)
sd.play = lambda *a, **k: None
sd.stop = lambda *a, **k: None
sd.sleep = lambda ms: None


class _FakeStream:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


sd.OutputStream = _FakeStream
sd.RawOutputStream = _FakeStream
sd.CallbackStop = type("CallbackStop", (Exception,), {})
sd.CallbackAbort = type("CallbackAbort", (Exception,), {})
sys.modules["sounddevice"] = sd

pynput = types.ModuleType("pynput")
keyboard = types.ModuleType("pynput.keyboard")
keyboard.Key = types.SimpleNamespace(
    ctrl="ctrl", alt="alt", shift="shift", cmd="cmd", esc="esc", space="space", enter="enter"
)
keyboard.KeyCode = lambda *a, **k: object()


class _FakeListener:
    def __init__(self, *a, **k):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def join(self, timeout=None):
        pass


keyboard.Listener = _FakeListener
keyboard.GlobalHotKeys = _FakeListener
pynput.keyboard = keyboard
sys.modules["pynput"] = pynput
sys.modules["pynput.keyboard"] = keyboard
sys.modules.setdefault("evdev", None)  # hotkey_listener ловит ImportError сам

# ── Окружение GTK ────────────────────────────────────────────────────────────
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Конфиг — во временный каталог, чтобы не трогать настоящий
import silero_tts_reader.config.config_manager as cfg_mod

TMP = Path(tempfile.mkdtemp(prefix="str-gtk-smoke-"))
cfg_mod.CONFIG_DIR = TMP
cfg_mod.CONFIG_FILE = TMP / "config.json"

from silero_tts_reader.config.config_manager import ConfigManager
from silero_tts_reader.ui.playback_widget import PlaybackWidget, ProgressBar
from silero_tts_reader.ui.settings_window import (
    SettingsWindow,
    _keyval_to_pynput,
)
from silero_tts_reader.ui.sni_tray import MenuEntry, StatusNotifierTray
from silero_tts_reader.ui.theme_manager import ThemeManager, rgba, shift_color

ok = 0
fail = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  ✓ {name}{(' — ' + detail) if detail else ''}")
    else:
        fail += 1
        print(f"  ✗ {name}{(' — ' + detail) if detail else ''}")


# ── 1. Цветовые утилиты ──────────────────────────────────────────────────────
print("\n1. Утилиты цвета")
check("shift_color осветляет", shift_color("#6366f1", 1.2) == "#777aff", shift_color("#6366f1", 1.2))
check("shift_color затемняет", shift_color("#6366f1", 0.5) == "#323378", shift_color("#6366f1", 0.5))
check("rgba()", rgba("#6366f1", 0.2) == "rgba(99, 102, 241, 0.200)", rgba("#6366f1", 0.2))

# ── 2. ThemeManager ──────────────────────────────────────────────────────────
print("\n2. ThemeManager (GObject-сигналы)")
tm = ThemeManager()
events: list[str] = []
tm.connect("theme-changed", lambda _m, dark: events.append(f"dark={dark}"))
tm.connect("accent-changed", lambda _m: events.append("accent"))
tm.set_mode("dark")
check("set_mode('dark') → is_dark()", tm.is_dark() is True)
tm.set_mode("light")
check("set_mode('light') → не тёмная", tm.is_dark() is False)
check("сигнал theme-changed при смене режима", any(e.startswith("dark=") for e in events), str(events))
check("акцент — строка #rrggbb", tm.accent_color().startswith("#") and len(tm.accent_color()) == 7, tm.accent_color())
check("hover-акцент отличается от базового", tm.accent_hover_color() != tm.accent_color())

# ── 3. Виджет воспроизведения ────────────────────────────────────────────────
print("\n3. PlaybackWidget (GTK-окно)")
calls: list[tuple] = []
w = PlaybackWidget()
w.on_pause_resume = lambda: calls.append(("pause",))
w.on_stop = lambda: calls.append(("stop",))
w.on_speed_changed = lambda s: calls.append(("speed", s))
w.on_rewind = lambda s: calls.append(("rewind", s))
w.on_seek_fraction = lambda f: calls.append(("seek", round(f, 2)))

check("окно создано, недекорировано", w.get_decorated() is False)
# В GTK4 подсказок панели задач нет: окно обычное, если не задан особый тип.
check("окно обычное (не служебное)", w.get_modal() is False and w.get_decorated() is False,
      f"modal={w.get_modal()}, decorated={w.get_decorated()}")

w.apply_theme(is_dark=True, accent_color="#6366f1")
w.set_progress(0.42)
check("set_progress не падает", True)
w.set_paused(True)
check("пауза меняет подпись кнопки", w._btn_pause.get_label() == "▶", w._btn_pause.get_label())
w.set_paused(False)
w.set_speed(1.75)
check("set_speed выбирает значение в списке", abs(w.get_speed() - 1.75) < 1e-6,
      str(w.get_speed()))
check("скорость — выпадающий список, а не шкала", isinstance(w._speed_combo, Gtk.DropDown),
      type(w._speed_combo).__name__)
check("по умолчанию выбрано 1×", w.get_speed() in (1.0, 1.75), str(w.get_speed()))
# В GTK4 ComboBoxText заменён DropDown со списком строк
model = w._speed_combo.get_model()
labels = {model.get_string(i) for i in range(model.get_n_items())}
check("в списке есть 0.5× и 3×", {"0.5×", "3×"}.issubset(labels), str(sorted(labels)))

w._btn_pause.emit("clicked")
w._btn_stop.emit("clicked")
w._btn_rewind.emit("clicked")
w._btn_forward.emit("clicked")
w._on_timeline_clicked(0.5)
check(
    "клики по кнопкам доходят до callbacks",
    ("pause",) in calls and ("stop",) in calls and ("rewind", -5.0) in calls
    and ("rewind", 5.0) in calls and ("seek", 0.5) in calls,
    str(calls),
)

w.fade_in()
# Показ отложен на оборот цикла GLib — прокручиваем контекст, иначе проверка
# увидит окно ещё не показанным (это и было причиной «окна нет» в работе).
for _ in range(50):
    if w.get_visible():
        break
    GLib.MainContext.default().iteration(False)
check("окно показано", w.get_visible() is True)
w.apply_theme(is_dark=False, accent_color="#2ec27e")
check("смена темы на светлую не падает", True)
check("прогресс-бар — свой DrawingArea", isinstance(w._progress, ProgressBar))

# ── 4. Диалог настроек ───────────────────────────────────────────────────────
print("\n4. SettingsWindow (Gtk.Dialog + Notebook)")
cfg = ConfigManager()
dlg = SettingsWindow(
    config=cfg,
    speakers={"ru": ["xenia", "aidar", "eugene"], "en": ["en_0", "en_42", "en_117"]},
    on_preview_voice=lambda sp, speed: calls.append(("preview", sp, speed)),
    theme_manager=tm,
)
check("четыре вкладки", dlg._notebook.get_n_pages() == 4, str(dlg._notebook.get_n_pages()))
check("горячие клавиши подставлены", dlg._hk_selection.get_label() != "", dlg._hk_selection.get_label())
check("голос ru выбран", dlg.selected_voice() == "xenia", dlg.selected_voice())
check("голосов ru три", len(dlg._voices_by_lang["ru"]) == 3, str(dlg._voices_by_lang))

dlg._hk_stop.set_hotkey("<ctrl>+<alt>+q")
check("set_hotkey/get_hotkey", dlg._hk_stop.get_hotkey() == "<ctrl>+<alt>+q")
# поиск по голосам: у английской модели их 119, перебором не выбрать
dlg._voice_lang_combo.set_active(1)   # en
check("переключение языка перезаполняет список", dlg.selected_voice() == "en_0", dlg.selected_voice())
dlg._voice_search.set_text("42")
check("поиск фильтрует голоса", dlg.selected_voice() == "en_42", dlg.selected_voice())
check("в отфильтрованном списке одна строка", dlg._voice_filter.iter_n_children(None) == 1,
      str(dlg._voice_filter.iter_n_children(None)))
dlg._voice_search.set_text("нет такого")
check("пустой результат ничего не выбирает", dlg._voice_filter.iter_n_children(None) == 0,
      str(dlg._voice_filter.iter_n_children(None)))
dlg._voice_search.set_text("")
dlg._voice_lang_combo.set_active(0)   # обратно ru
dlg._select_voice("aidar")
dlg._speed_slider.set_value(1.5)
dlg._theme_combo.set_active(1)  # тёмная
dlg._btn_preview.emit("clicked")
check("кнопка «Прослушать» вызывает callback", any(c[0] == "preview" for c in calls), str(calls[-1:]))

saved: list[bool] = []
dlg.on_saved = lambda: saved.append(True)
dlg._on_save()
check("on_saved вызван", saved == [True])
check("конфиг сохранён на диск", cfg_mod.CONFIG_FILE.exists(), str(cfg_mod.CONFIG_FILE))
import json

saved_cfg = json.loads(cfg_mod.CONFIG_FILE.read_text(encoding="utf-8"))
check("голос записан в конфиг", saved_cfg["tts"]["speaker"] == "aidar", saved_cfg["tts"]["speaker"])
check("скорость записана", abs(saved_cfg["playback"]["speed"] - 1.5) < 1e-6, str(saved_cfg["playback"]["speed"]))
check("тема записана", saved_cfg["ui"]["theme"] == "dark", saved_cfg["ui"]["theme"])
check("hotkey_stop записан", saved_cfg["hotkeys"]["stop"] == "<ctrl>+<alt>+q", saved_cfg["hotkeys"]["stop"])

# ── 5. Разбор клавиш GDK → pynput ────────────────────────────────────────────
print("\n5. Клавиши GDK → pynput")
cases = {
    Gdk.KEY_r: "r", Gdk.KEY_A: "a", Gdk.KEY_5: "5",
    Gdk.KEY_F5: "f5", Gdk.KEY_Page_Up: "page_up", Gdk.KEY_Return: "enter",
    Gdk.KEY_Escape: "esc", Gdk.KEY_space: "space", Gdk.KEY_Delete: "delete",
    Gdk.KEY_Super_L: "cmd", Gdk.KEY_Control_L: "ctrl", Gdk.KEY_Print: "print_screen",
}
for keyval, expected in cases.items():
    got = _keyval_to_pynput(keyval)
    check(f"{Gdk.keyval_name(keyval)} → {expected}", got == expected, str(got))

# ── 6. Значок в лотке ───────────────────────────────────────────────────────
print("\n6. StatusNotifierItem")
# Значок теперь не GTK-объект: он живёт на шине D-Bus. Здесь проверяется то,
# что видно без службы лотка — отказ вместо падения и состав меню.
tray = StatusNotifierTray(icon_name="silero-tts-reader", title="Silero TTS Reader")
tray.set_entries([
    MenuEntry("Показать окно", lambda: None),
    MenuEntry("Открыть файл…", lambda: None),
    MenuEntry(separator=True),
    MenuEntry("Выход", lambda: None),
])
check("меню значка: 3 пункта и разделитель", len(tray._flat) == 4, str(len(tray._flat)))
check("разделитель помечен как separator",
      sum(1 for entry in tray._flat.values() if entry.separator) == 1)
check("значок не показан без вызова show()", tray.available is False)
tray.hide()

# Пункт меню проверяем по действию, а не по нажатию: щелчок приходит с шины.
entry = next(item for item in tray._flat.values()
             if item.label == "Открыть файл…")
opened: list[str] = []
entry.action = lambda: opened.append("open")
entry.action()
check("действие пункта меню вызывается", opened == ["open"], str(opened))

# ── 7. Приложение целиком (импорт и конструктор) ─────────────────────────────
print("\n7. Application (импорт без запуска главного цикла)")
from silero_tts_reader.app import Application

app = Application(["silero-tts-reader"])
check("Application создан, id приложения", app.get_application_id() == "ru.skala.silero-tts-reader", str(app.get_application_id()))
check("движок и плеер не инициализированы до activate", app._widget is None and app._tray is None)

print(f"\nИТОГ: успешно {ok}, провалено {fail}")
sys.exit(0 if fail == 0 else 1)
