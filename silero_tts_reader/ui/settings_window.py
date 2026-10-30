"""Диалог настроек: горячие клавиши, голос/аудио, оформление, ИИ (GTK3).

Переписано с Qt на PyGObject: ``QDialog`` → ``Gtk.Dialog``, ``QTabWidget`` →
``Gtk.Notebook``, ``QPushButton`` с перехватом клавиш → ``Gtk.Button`` с
обработчиками ``key-press-event``/``key-release-event``, ``QMessageBox`` →
``Gtk.MessageDialog``. Вместо сигнала ``settings_saved`` — callback ``on_saved``.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, Gtk  # noqa: E402

from ..config.config_manager import ConfigManager  # noqa: E402
from .. import i18n
from ..i18n import _
from ..core.audio_player import SPEED_AVAILABLE  # noqa: E402
from ..core.hotkey_listener import HotkeyListener  # noqa: E402
from ..core.text_processor import TextProcessor  # noqa: E402
from .theme_manager import ThemeManager, rgba, shift_color  # noqa: E402

RESPONSE_RESET = 1


# ── Имена клавиш GDK → pynput ────────────────────────────────────────────────

_MODIFIER_KEYVALS = {
    Gdk.KEY_Control_L: "ctrl",
    Gdk.KEY_Control_R: "ctrl",
    Gdk.KEY_Alt_L: "alt",
    Gdk.KEY_Alt_R: "alt",
    Gdk.KEY_Shift_L: "shift",
    Gdk.KEY_Shift_R: "shift",
    Gdk.KEY_Super_L: "cmd",
    Gdk.KEY_Super_R: "cmd",
    Gdk.KEY_Meta_L: "cmd",
    Gdk.KEY_Meta_R: "cmd",
}

_SPECIAL_KEYNAMES = {
    "Return": "enter",
    "KP_Enter": "enter",
    "Escape": "esc",
    "BackSpace": "backspace",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Page_Up": "page_up",
    "Page_Down": "page_down",
    "space": "space",
    "Tab": "tab",
    "Caps_Lock": "caps_lock",
    "Num_Lock": "num_lock",
    "Scroll_Lock": "scroll_lock",
    "Pause": "pause",
    "Print": "print_screen",
    "Menu": "menu",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
}


def _keyval_to_pynput(keyval: int) -> str | None:
    """Имя клавиши в формате pynput (``a``, ``f5``, ``page_up``, ``<ctrl>``…)."""
    if keyval in _MODIFIER_KEYVALS:
        return _MODIFIER_KEYVALS[keyval]

    name = Gdk.keyval_name(keyval)
    if not name:
        return None

    if name in _SPECIAL_KEYNAMES:
        return _SPECIAL_KEYNAMES[name]

    if name.startswith("F") and name[1:].isdigit():  # F1..F35
        return name.lower()

    if len(name) == 1:
        return name.lower()

    # Прочее — в нижнем регистре, как это делает pynput (например «KP_1» → «kp_1»).
    return name.lower()


def _modifiers_to_parts(state: Gdk.ModifierType) -> list[str]:
    parts: list[str] = []
    if state & Gdk.ModifierType.CONTROL_MASK:
        parts.append("<ctrl>")
    if state & Gdk.ModifierType.MOD1_MASK:
        parts.append("<alt>")
    if state & Gdk.ModifierType.SHIFT_MASK:
        parts.append("<shift>")
    if state & Gdk.ModifierType.SUPER_MASK:
        parts.append("<cmd>")
    return parts


class HotkeyCapture(Gtk.Button):
    """Кнопка, которая по клику перехватывает нажатие и запоминает комбинацию."""

    def __init__(self) -> None:
        super().__init__(label="")  # с текстом создаётся Gtk.Label-ребёнок
        self.on_captured: Callable[[str], None] | None = None
        self._capturing = False
        self._current_hotkey = ""
        self._draft = ""
        self._accent_color = "#6366f1"

        self.set_can_focus(True)
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_halign(Gtk.Align.FILL)
        self.get_child().set_xalign(0.0)

        cursor = Gdk.Cursor.new_from_name(self.get_display(), "pointer")
        self.connect("realize", lambda w: w.get_window().set_cursor(cursor))
        self.connect("clicked", self._start_capture)
        self.connect("focus-out-event", self._on_focus_out)
        self._update_style()

    # ── Внешний интерфейс ────────────────────────────────────────────────────

    def set_accent(self, accent_color: str = "#6366f1") -> None:
        self._accent_color = accent_color
        self._update_style()

    def set_hotkey(self, hotkey_str: str) -> None:
        self._current_hotkey = hotkey_str or ""
        self._capturing = False
        self._draft = ""
        self.set_label(
            HotkeyListener.format_hotkey(self._current_hotkey)
            if self._current_hotkey
            else "Кликните для выбора…"
        )
        self._update_style()

    def get_hotkey(self) -> str:
        return self._current_hotkey

    # ── Захват ───────────────────────────────────────────────────────────────

    def _start_capture(self, *_args) -> None:
        self._capturing = True
        self._draft = ""
        self.set_label("Нажмите новую клавишу…")
        self._update_style()
        self.grab_focus()

    def _on_focus_out(self, *_args) -> bool:
        if self._capturing:
            self._cancel_capture()
        return False

    def _cancel_capture(self) -> None:
        self._capturing = False
        self._draft = ""
        self.set_label(
            HotkeyListener.format_hotkey(self._current_hotkey)
            if self._current_hotkey
            else "Кликните для выбора…"
        )
        self._update_style()

    def _commit(self, hotkey: str) -> None:
        self.set_hotkey(hotkey)
        if self.on_captured:
            self.on_captured(hotkey)

    def do_key_press_event(self, event) -> bool:  # noqa: N802
        if not self._capturing:
            return Gtk.Button.do_key_press_event(self, event)

        state = event.state & Gtk.accelerator_get_default_mod_mask()
        parts = _modifiers_to_parts(state)

        if event.keyval in _MODIFIER_KEYVALS:
            # Нажали только модификатор: показываем черновик, решение — на отпускании.
            mod = f"<{_MODIFIER_KEYVALS[event.keyval]}>"
            if mod not in parts:
                parts.append(mod)
            self._draft = "+".join(parts)
            self.set_label(HotkeyListener.format_hotkey(self._draft) + "…")
            return True

        key_name = _keyval_to_pynput(event.keyval)
        if key_name:
            parts.append(key_name)
            self._draft = ""
            self._commit("+".join(parts))
        return True

    def do_key_release_event(self, event) -> bool:  # noqa: N802
        if not self._capturing:
            return Gtk.Button.do_key_release_event(self, event)

        if event.keyval in _MODIFIER_KEYVALS and self._draft:
            state = event.state & Gtk.accelerator_get_default_mod_mask()
            if not state:  # отпустили последний модификатор — комбинация готова
                draft = self._draft
                self._draft = ""
                self._commit(draft)
        return True

    # ── Оформление ───────────────────────────────────────────────────────────

    def _update_style(self) -> None:
        accent = self._accent_color
        if self._capturing:
            css = f"""
            button {{
                background-image: none;
                background-color: {rgba(accent, 0.2)};
                color: #ffffff;
                border: 2px solid {accent};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                font-weight: bold;
                box-shadow: none;
                text-shadow: none;
            }}
            """
        else:
            css = f"""
            button {{
                background-image: none;
                background-color: rgba(255, 255, 255, 0.06);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                box-shadow: none;
                text-shadow: none;
            }}
            button:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                border-color: {accent};
                color: #ffffff;
            }}
            button:focus {{
                border-color: {accent};
                color: #ffffff;
            }}
            """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        self.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


class SettingsWindow(Gtk.Dialog):
    """Диалог настроек: горячие клавиши + голос/аудио + оформление + ИИ."""

    def __init__(
        self,
        config: ConfigManager,
        speakers: list[str] | dict[str, list[str]],
        on_preview_voice: Callable[[str, float], None],
        theme_manager: ThemeManager | None = None,
        parent: Gtk.Window | None = None,
    ) -> None:
        super().__init__(title=_("Настройки — Silero TTS Reader"), transient_for=parent, modal=True)
        self._config = config
        # Голоса по языкам: у русской модели шесть голосов, у английской — 119,
        # поэтому выбор идёт с поиском, а не перебором в выпадающем списке.
        if isinstance(speakers, dict):
            self._voices_by_lang = {lang: list(v) for lang, v in speakers.items() if v}
        else:
            self._voices_by_lang = {"ru": list(speakers)} if speakers else {}
        if not self._voices_by_lang:
            self._voices_by_lang = {"ru": ["xenia"]}
        self._speakers = sorted({v for values in self._voices_by_lang.values() for v in values})
        self._on_preview_voice = on_preview_voice
        self._theme_manager = theme_manager
        self._pending: dict[str, object] = {}

        self.on_saved: Callable[[], None] | None = None

        self.set_default_size(620, 500)
        self.set_resizable(False)
        self.set_border_width(16)

        if self._theme_manager:
            self._theme_manager.connect("theme-changed", lambda *_: self._apply_theme())
            self._theme_manager.connect("accent-changed", lambda *_: self._apply_theme())
            is_dark = self._theme_manager.is_dark()
        else:
            is_dark = True

        self._build_ui()
        self._apply_theme(is_dark)
        self.connect("response", self._on_response)

    # ── Тема ─────────────────────────────────────────────────────────────────

    def _apply_theme(self, is_dark: bool | None = None) -> None:
        if is_dark is None:
            is_dark = self._theme_manager.is_dark() if self._theme_manager else True
        accent = self._theme_manager.accent_color() if self._theme_manager else "#6366f1"
        hover = self._theme_manager.accent_hover_color() if self._theme_manager else "#4f46e5"

        for widget in ("_hk_selection", "_hk_clipboard", "_hk_stop"):
            if hasattr(self, widget):
                getattr(self, widget).set_accent(accent)

        if is_dark:
            bg, fg, muted, pane = "#18181b", "#f4f4f5", "#a1a1aa", "#27272a"
            entry_bg, border, reset_bg, reset_fg = (
                rgba("#ffffff", 0.06),
                rgba("#ffffff", 0.12),
                rgba("#ef4444", 0.2),
                "#ef4444",
            )
            reset_border = rgba("#ef4444", 0.4)
        else:
            bg, fg, muted, pane = "#f8fafc", "#0f172a", "#64748b", "#ffffff"
            entry_bg, border, reset_bg, reset_fg = "#f1f5f9", "#cbd5e1", "#fee2e2", "#dc2626"
            reset_border = "#fca5a5"

        provider = Gtk.CssProvider()
        provider.load_from_data(
            f"""
            dialog {{ background-color: {bg}; }}
            dialog label {{ color: {fg}; font-size: 13px; }}
            notebook > header {{ background-color: transparent; border: none; }}
            notebook > header tab {{
                background-color: transparent;
                color: {muted};
                padding: 8px 22px;
                border-bottom: 2px solid transparent;
                font-size: 16px;
            }}
            notebook > header tab:checked {{ color: {accent}; border-bottom: 2px solid {accent}; }}
            notebook stack {{ background-color: {pane}; border: 1px solid {border}; border-radius: 8px; }}
            frame > label {{ color: {accent}; font-size: 11px; font-weight: 600; }}
            frame {{ border: 1px solid {border}; border-radius: 8px; }}
            entry, spinbutton {{
                background-image: none;
                background-color: {entry_bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px 10px;
                min-height: 18px;
            }}
            combobox button.combo {{
                background-image: none;
                background-color: {entry_bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px 10px;
                min-width: 160px;
            }}
            checkbutton {{ color: {fg}; font-size: 13px; }}
            check {{ background-color: {entry_bg}; border: 1px solid {border}; border-radius: 4px; min-width: 16px; min-height: 16px; }}
            check:checked {{ background-color: {accent}; border-color: {accent}; color: #ffffff; }}
            button {{
                background-image: none;
                background-color: {accent};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 16px;
                font-size: 13px;
                box-shadow: none;
                text-shadow: none;
            }}
            button:hover {{ background-color: {hover}; color: #ffffff; }}
            button.reset {{ background-color: {reset_bg}; color: {reset_fg}; border: 1px solid {reset_border}; }}
            button.reset:hover {{ background-color: {reset_border}; color: {reset_fg}; }}
            button.secondary {{ background-color: {entry_bg}; color: {muted}; }}
            textview, textview text {{ background-color: {entry_bg}; color: {fg}; }}
            textview {{ border: 1px solid {border}; border-radius: 6px; }}
            scale trough {{ background-color: {border}; border-radius: 2px; min-height: 4px; }}
            scale highlight {{ background-color: {accent}; border-radius: 2px; }}
            scale slider {{ background-color: {accent}; border: none; border-radius: 6px; min-width: 12px; min-height: 12px; margin: -4px 0; }}
            """.encode("utf-8")
        )
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._hint_color = muted

    # ── Сборка интерфейса ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        content = self.get_content_area()
        content.set_spacing(12)

        self._notebook = Gtk.Notebook()
        self._notebook.append_page(self._build_hotkeys_tab(), Gtk.Label(label="⌨️"))
        self._notebook.append_page(self._build_voice_tab(), Gtk.Label(label="🎭"))
        self._notebook.append_page(self._build_appearance_tab(), Gtk.Label(label="🎨"))
        self._notebook.append_page(self._build_ai_tab(), Gtk.Label(label="🤖"))
        self._notebook.set_tab_pos(Gtk.PositionType.TOP)
        for index, tip in enumerate(
            ("Горячие клавиши", "Голос и аудио", "Внешний вид", "ИИ & Транслитерация")
        ):
            self._notebook.get_nth_page(index).set_tooltip_text(tip)
        content.pack_start(self._notebook, True, True, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(button_row, False, False, 0)

        self._btn_reset = Gtk.Button(label=_("Сброс к умолчаниям"))
        self._btn_reset.get_style_context().add_class("reset")
        self._btn_reset.connect("clicked", self._on_reset)
        button_row.pack_start(self._btn_reset, False, False, 0)
        button_row.pack_start(Gtk.Box(), True, True, 0)  # растяжка

        self._btn_cancel = Gtk.Button(label=_("Отмена"))
        self._btn_cancel.get_style_context().add_class("secondary")
        self._btn_cancel.connect("clicked", lambda _b: self.response(Gtk.ResponseType.CANCEL))
        button_row.pack_start(self._btn_cancel, False, False, 0)

        self._btn_save = Gtk.Button(label=_("Сохранить"))
        self._btn_save.connect("clicked", lambda _b: self.response(Gtk.ResponseType.OK))
        button_row.pack_start(self._btn_save, False, False, 0)

        self.show_all()

    # ── Вкладка «Горячие клавиши» ────────────────────────────────────────────

    def _build_hotkeys_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(12)

        group = Gtk.Frame(label=_("ГЛОБАЛЬНЫЕ ГОРЯЧИЕ КЛАВИШИ"))
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.set_border_width(10)
        group.add(grid)
        page.pack_start(group, False, False, 0)

        accent = self._theme_manager.accent_color() if self._theme_manager else "#6366f1"

        self._hk_selection = HotkeyCapture()
        self._hk_selection.set_accent(accent)
        self._hk_selection.set_hotkey(self._config.hotkey_speak_selection)
        self._hk_selection.on_captured = lambda v: self._pending.update(
            {"hotkey_speak_selection": v}
        )
        grid.attach(self._label(_("Озвучить выделенное:")), 0, 0, 1, 1)
        grid.attach(self._hk_selection, 1, 0, 1, 1)

        self._hk_clipboard = HotkeyCapture()
        self._hk_clipboard.set_accent(accent)
        self._hk_clipboard.set_hotkey(self._config.hotkey_speak_clipboard)
        self._hk_clipboard.on_captured = lambda v: self._pending.update(
            {"hotkey_speak_clipboard": v}
        )
        grid.attach(self._label(_("Озвучить буфер обмена:")), 0, 1, 1, 1)
        grid.attach(self._hk_clipboard, 1, 1, 1, 1)

        self._hk_stop = HotkeyCapture()
        self._hk_stop.set_accent(accent)
        self._hk_stop.set_hotkey(self._config.hotkey_stop)
        self._hk_stop.on_captured = lambda v: self._pending.update({"hotkey_stop": v})
        grid.attach(self._label(_("Остановить:")), 0, 2, 1, 1)
        grid.attach(self._hk_stop, 1, 2, 1, 1)

        hint = Gtk.Label(
            label=_("💡 Кликните по полю и нажмите желаемую комбинацию клавиш.\n"
                    "Работает только на X11 / XWayland.")
        )
        hint.set_xalign(0.0)
        hint.set_line_wrap(True)
        hint.set_name("hint")
        page.pack_start(hint, False, False, 0)

        page.pack_start(Gtk.Box(), True, True, 0)
        return page

    # ── Вкладка «Голос и аудио» ──────────────────────────────────────────────

    def _build_voice_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(12)

        voice_group = Gtk.Frame(label=_("ГОЛОС"))
        voice_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        voice_box.set_border_width(10)
        voice_group.add(voice_box)
        page.pack_start(voice_group, False, False, 0)

        # Язык модели: голоса у языков разные, выбирать их вперемешку нельзя
        lang_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lang_row.pack_start(self._label(_("Язык:")), False, False, 0)
        self._voice_lang_combo = Gtk.ComboBoxText()
        for lang in self._voices_by_lang:
            self._voice_lang_combo.append_text(lang)
        self._voice_lang_combo.set_active(0)
        self._voice_lang_combo.connect("changed", lambda _c: self._reload_voices())
        lang_row.pack_start(self._voice_lang_combo, False, False, 0)
        voice_box.pack_start(lang_row, False, False, 0)

        # Поиск: у английской модели 119 голосов (en_0…en_117)
        self._voice_search = Gtk.Entry()
        self._voice_search.set_placeholder_text(_("Поиск голоса…"))
        self._voice_search.connect("changed", lambda _e: self._on_voice_search())
        voice_box.pack_start(self._voice_search, False, False, 0)

        self._voice_store = Gtk.ListStore(str)
        self._voice_filter = self._voice_store.filter_new()
        self._voice_filter.set_visible_func(self._voice_visible)
        self._voice_view = Gtk.TreeView(model=self._voice_filter)
        self._voice_view.set_headers_visible(False)
        renderer = Gtk.CellRendererText()
        self._voice_view.append_column(Gtk.TreeViewColumn(_("Голос"), renderer, text=0))
        self._voice_view.get_selection().connect("changed", self._on_voice_selected)
        voice_scroll = Gtk.ScrolledWindow()
        voice_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        voice_scroll.set_min_content_height(120)
        voice_scroll.add(self._voice_view)
        voice_box.pack_start(voice_scroll, True, True, 0)

        preview_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._btn_preview = Gtk.Button(label=_("▶  Прослушать"))
        self._btn_preview.connect("clicked", self._on_preview)
        preview_row.pack_start(self._btn_preview, False, False, 0)
        self._voice_count = Gtk.Label(label="")
        preview_row.pack_start(self._voice_count, False, False, 0)
        voice_box.pack_start(preview_row, False, False, 0)

        self._reload_voices()

        speed_group = Gtk.Frame(label=_("СКОРОСТЬ ВОСПРОИЗВЕДЕНИЯ ПО УМОЛЧАНИЮ"))
        speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        speed_row.set_border_width(10)
        speed_group.add(speed_row)
        page.pack_start(speed_group, False, False, 0)

        self._speed_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 3.0, 0.25)
        self._speed_slider.set_draw_value(False)
        self._speed_slider.set_value(max(0.5, min(3.0, self._config.speed)))
        self._speed_slider.set_sensitive(SPEED_AVAILABLE)
        self._speed_slider.connect("value-changed", self._on_speed_slider)
        speed_row.pack_start(self._speed_slider, True, True, 0)

        self._speed_label = Gtk.Label(label=f"{self._config.speed:.2g}×")
        self._speed_label.set_width_chars(4)
        speed_row.pack_start(self._speed_label, False, False, 0)

        if not SPEED_AVAILABLE:
            no_rb = Gtk.Label(label=_("(требуется rubberband-cli)"))
            no_rb.set_name("hint")
            speed_row.pack_start(no_rb, False, False, 0)

        page.pack_start(Gtk.Box(), True, True, 0)
        return page

    # ── Вкладка «Оформление» ─────────────────────────────────────────────────

    def _build_appearance_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(12)

        group = Gtk.Frame(label=_("ОФОРМЛЕНИЕ И ТЕМА ИНТЕРФЕЙСА"))
        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        grid.set_border_width(10)
        group.add(grid)
        page.pack_start(group, False, False, 0)

        self._theme_combo = Gtk.ComboBoxText()
        for label, value in (
            ("Авто (следовать системной теме)", "system"),
            ("Тёмная тема", "dark"),
            ("Светлая тема", "light"),
        ):
            self._theme_combo.append(value, label)

        current_theme = self._pending.get("theme", self._config.theme)
        active = self._theme_combo.get_model()
        for index, row in enumerate(active):
            if row[0] == current_theme:
                self._theme_combo.set_active(index)
                break
        if self._theme_combo.get_active() < 0:
            self._theme_combo.set_active(0)
        self._theme_combo.connect("changed", self._on_theme_changed)
        grid.attach(self._label(_("Тема приложения:")), 0, 0, 1, 1)
        grid.attach(self._theme_combo, 1, 0, 1, 1)

        self._language_combo = Gtk.ComboBoxText()
        self._language_combo.append("auto", _("Авто (из системы)"))
        for code, name in i18n.AVAILABLE.items():
            self._language_combo.append(code, name)
        current_language = self._config.ui_language
        if not self._language_combo.set_active_id(current_language):
            self._language_combo.set_active_id("auto")
        self._language_combo.connect(
            "changed", lambda w: self._pending.update({"ui_language": w.get_active_id()})
        )
        grid.attach(self._label(_("Язык интерфейса:")), 0, 1, 1, 1)
        grid.attach(self._language_combo, 1, 1, 1, 1)

        self._accent_chk = Gtk.CheckButton(label=_("Использовать системный акцентный цвет"))
        use_sys_acc = self._pending.get("use_system_accent", self._config.use_system_accent)
        self._accent_chk.set_active(bool(use_sys_acc))
        self._accent_chk.connect("toggled", self._on_accent_toggled)
        grid.attach(self._accent_chk, 0, 2, 2, 1)

        page.pack_start(Gtk.Box(), True, True, 0)
        return page

    # ── Вкладка «ИИ и транслитерация» ────────────────────────────────────────

    def _build_ai_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(12)

        trans_group = Gtk.Frame(label=_("ТРАНСЛИТЕРАЦИЯ"))
        trans_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        trans_box.set_border_width(10)
        trans_group.add(trans_box)
        page.pack_start(trans_group, False, False, 0)

        self._chk_ignore_urls = Gtk.CheckButton(label=_("Игнорировать ссылки (http/https/www)"))
        self._chk_ignore_urls.set_active(self._config.ignore_urls)
        trans_box.pack_start(self._chk_ignore_urls, False, False, 0)

        self._chk_transliterate = Gtk.CheckButton(
            label=_("Переписывать латинские/иностранные слова кириллицей")
        )
        self._chk_transliterate.set_active(self._config.transliteration_enabled)
        trans_box.pack_start(self._chk_transliterate, False, False, 0)

        self._chk_use_llm = Gtk.CheckButton(
            label=_("Использовать ИИ (LLM) для фонетической транскрипции")
        )
        self._chk_use_llm.set_active(self._config.transliteration_use_llm)
        self._chk_use_llm.connect(
            "toggled", lambda c: self._chk_transliterate.set_active(True) if c.get_active() else None
        )
        trans_box.pack_start(self._chk_use_llm, False, False, 0)

        llm_group = Gtk.Frame(label=_("НАСТРОЙКИ ИИ-МОДЕЛИ (API)"))
        grid = Gtk.Grid(row_spacing=8, column_spacing=12)
        grid.set_border_width(10)
        llm_group.add(grid)
        page.pack_start(llm_group, False, False, 0)

        self._llm_provider_combo = Gtk.ComboBoxText()
        for label, value in (
            ("Ollama (Локальный)", "ollama"),
            ("OpenAI (Облачный)", "openai"),
            ("Пользовательский (Custom API)", "custom"),
        ):
            self._llm_provider_combo.append(value, label)
        curr_prov = self._config.transliteration_provider
        provider_model = self._llm_provider_combo.get_model()
        for index, row in enumerate(provider_model):
            if row[0] == curr_prov:
                self._llm_provider_combo.set_active(index)
                break
        if self._llm_provider_combo.get_active() < 0:
            self._llm_provider_combo.set_active(0)
        self._llm_provider_combo.connect("changed", self._on_llm_provider_changed)
        grid.attach(self._label(_("Провайдер:")), 0, 0, 1, 1)
        grid.attach(self._llm_provider_combo, 1, 0, 1, 1)

        self._txt_base_url = Gtk.Entry(text=self._config.transliteration_base_url)
        self._txt_base_url.set_placeholder_text("http://localhost:11434/v1")
        grid.attach(self._label("API Base URL:"), 0, 1, 1, 1)
        grid.attach(self._txt_base_url, 1, 1, 1, 1)

        self._txt_api_key = Gtk.Entry(text=self._config.transliteration_api_key)
        self._txt_api_key.set_visibility(False)
        self._txt_api_key.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self._txt_api_key.set_placeholder_text("ollama / sk-…")
        grid.attach(self._label("API Key:"), 0, 2, 1, 1)
        grid.attach(self._txt_api_key, 1, 2, 1, 1)

        self._txt_model = Gtk.Entry(text=self._config.transliteration_model)
        self._txt_model.set_placeholder_text("qwen2.5 / llama3 / gpt-4o-mini")
        grid.attach(self._label(_("Название модели:")), 0, 3, 1, 1)
        grid.attach(self._txt_model, 1, 3, 1, 1)

        self._txt_system_prompt = Gtk.TextView()
        self._txt_system_prompt.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._txt_system_prompt.get_buffer().set_text(self._config.transliteration_system_prompt)
        self._txt_system_prompt.set_size_request(-1, 65)
        self._txt_system_prompt.set_tooltip_text(_("Системный промпт ИИ с правилом расстановки знака ударения '+'"))
        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        prompt_scroll.add(self._txt_system_prompt)
        grid.attach(self._label(_("Системный промпт ИИ:")), 0, 4, 1, 1)
        grid.attach(prompt_scroll, 1, 4, 1, 1)

        self._btn_test_llm = Gtk.Button(label=_("🧪 Проверить подключение ИИ"))
        self._btn_test_llm.connect("clicked", self._on_test_llm_connection)
        grid.attach(self._btn_test_llm, 1, 5, 1, 1)

        hint = Gtk.Label(
            label=_("💡 При недоступности ИИ или отключении опции применяется быстрый "
                    "встроенный офлайн-фонетизатор.")
        )
        hint.set_xalign(0.0)
        hint.set_line_wrap(True)
        hint.set_name("hint")
        page.pack_start(hint, False, False, 0)

        page.pack_start(Gtk.Box(), True, True, 0)
        return page

    # ── Вспомогательное ──────────────────────────────────────────────────────

    @staticmethod
    def _label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_xalign(0.0)
        return label

    # ── Обработчики ──────────────────────────────────────────────────────────

    def _on_llm_provider_changed(self, combo: Gtk.ComboBoxText) -> None:
        prov = combo.get_active_id()
        if prov == "ollama":
            self._txt_base_url.set_text("http://localhost:11434/v1")
            self._txt_api_key.set_text("ollama")
            if not self._txt_model.get_text():
                self._txt_model.set_text("qwen2.5")
        elif prov == "openai":
            self._txt_base_url.set_text("https://api.openai.com/v1")
            if not self._txt_model.get_text():
                self._txt_model.set_text("gpt-4o-mini")

    def _on_test_llm_connection(self, _button) -> None:
        buffer = self._txt_system_prompt.get_buffer()
        prompt = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        ).strip()

        ok, msg = TextProcessor.test_llm_connection(
            base_url=self._txt_base_url.get_text().strip(),
            api_key=self._txt_api_key.get_text().strip(),
            model=self._txt_model.get_text().strip(),
            timeout=4.0,
            system_prompt=prompt or None,
        )
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO if ok else Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=_("Тест ИИ API") if ok else "Ошибка ИИ API",
        )
        dialog.format_secondary_text(msg)
        dialog.run()
        dialog.destroy()

    def _on_theme_changed(self, combo: Gtk.ComboBoxText) -> None:
        theme = combo.get_active_id()
        if not theme:
            return
        self._pending["theme"] = theme
        if self._theme_manager:
            self._theme_manager.set_mode(theme)

    def _on_accent_toggled(self, check: Gtk.CheckButton) -> None:
        state = check.get_active()
        self._pending["use_system_accent"] = state
        if self._theme_manager:
            self._theme_manager.use_system_accent = state

    # ── Голоса: язык + поиск ─────────────────────────────────────────────────

    def _current_language(self) -> str:
        index = self._voice_lang_combo.get_active()
        languages = list(self._voices_by_lang)
        if 0 <= index < len(languages):
            return languages[index]
        return languages[0]

    def _reload_voices(self) -> None:
        """Перезаполнить список голосов выбранного языка и выделить текущий."""
        language = self._current_language()
        self._voice_store.clear()
        for voice in self._voices_by_lang.get(language, []):
            self._voice_store.append([voice])

        pending = self._pending.get("speakers")
        selected = pending.get(language) if isinstance(pending, dict) else None
        self._voice_search.set_text("")
        self._voice_filter.refilter()
        self._select_voice(selected or self._config.speaker_for(language))
        self._update_voice_count()

    def _update_voice_count(self) -> None:
        shown = self._voice_filter.iter_n_children(None)
        total = len(self._voices_by_lang.get(self._current_language(), []))
        self._voice_count.set_text(_("показано {shown} из {total}").format(
            shown=shown, total=total))

    def _select_voice(self, voice: str) -> None:
        for row in self._voice_filter:
            if row[0] == voice:
                self._voice_view.get_selection().select_iter(row.iter)
                return

    def _voice_visible(self, model, tree_iter, _data=None) -> bool:
        query = self._voice_search.get_text().strip().lower()
        if not query:
            return True
        return query in model[tree_iter][0].lower()

    def _on_voice_search(self) -> None:
        self._voice_filter.refilter()
        self._update_voice_count()
        # если текущий голос отфильтрован — выделяем первый видимый
        if self._voice_view.get_selection().get_selected()[1] is None:
            first = self._voice_filter.get_iter_first()
            if first is not None:
                self._voice_view.get_selection().select_iter(first)

    def _on_voice_selected(self, selection) -> None:
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            return
        voice = model[tree_iter][0]
        language = self._current_language()
        speakers = dict(self._pending.get("speakers") or self._config.speakers)
        speakers[language] = voice
        self._pending["speakers"] = speakers

    def selected_voice(self) -> str:
        """Голос, выбранный в списке (для предпросмотра и тестов)."""
        model, tree_iter = self._voice_view.get_selection().get_selected()
        if tree_iter is None:
            return self._config.speaker_for(self._current_language())
        return model[tree_iter][0]

    def _on_preview(self, _button=None) -> None:
        speed = round(self._speed_slider.get_value() * 4) / 4
        self._on_preview_voice(self.selected_voice(), speed)

    def _on_speed_slider(self, scale: Gtk.Scale) -> None:
        speed = round(scale.get_value() * 4) / 4
        self._speed_label.set_text(f"{speed:.2g}×")
        self._pending["speed"] = speed

    def _on_response(self, _dialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            self._on_save()
        elif response == RESPONSE_RESET:
            self._on_reset()
        else:
            self.destroy()

    def _on_save(self) -> None:
        # Горячие клавиши читаем прямо из полей — в pending они попадают только при захвате
        if hasattr(self, "_hk_selection"):
            self._config.hotkey_speak_selection = self._hk_selection.get_hotkey()
        if hasattr(self, "_hk_clipboard"):
            self._config.hotkey_speak_clipboard = self._hk_clipboard.get_hotkey()
        if hasattr(self, "_hk_stop"):
            self._config.hotkey_stop = self._hk_stop.get_hotkey()

        # ИИ и транслитерация
        self._config.ignore_urls = self._chk_ignore_urls.get_active()
        use_llm = self._chk_use_llm.get_active()
        self._config.transliteration_use_llm = use_llm
        self._config.transliteration_enabled = self._chk_transliterate.get_active() or use_llm
        self._config.transliteration_provider = self._llm_provider_combo.get_active_id() or "custom"
        self._config.transliteration_base_url = self._txt_base_url.get_text().strip()
        self._config.transliteration_api_key = self._txt_api_key.get_text().strip()
        self._config.transliteration_model = self._txt_model.get_text().strip()
        buffer = self._txt_system_prompt.get_buffer()
        self._config.transliteration_system_prompt = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        ).strip()

        speakers = self._pending.get("speakers")
        if isinstance(speakers, dict):
            for language, voice in speakers.items():
                self._config.set_speaker(str(language), str(voice))

        for key in ("speed", "theme", "use_system_accent"):
            if key in self._pending:
                setattr(self._config, key, self._pending[key])

        language = self._pending.get("ui_language")
        if language and language != self._config.ui_language:
            self._config.ui_language = language
            # Пересобрать интерфейс на лету нельзя без перезагрузки виджетов,
            # поэтому честно говорим: применится после перезапуска.
            self._language_changed = True

        self._config.save()
        self._pending.clear()
        if self.on_saved:
            self.on_saved()
        if getattr(self, "_language_changed", False):
            self._show_language_notice()
        self.destroy()

    def _show_language_notice(self) -> None:
        """Сказать, что язык применится после перезапуска."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=_("Язык интерфейса изменится после перезапуска приложения."),
        )
        dialog.set_title(_("Язык интерфейса"))
        dialog.run()
        dialog.destroy()

    def _on_reset(self) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Сбросить все настройки к значениям по умолчанию?"),
        )
        dialog.set_title(_("Сброс настроек"))
        answer = dialog.run()
        dialog.destroy()
        if answer == Gtk.ResponseType.YES:
            self._config.reset_to_defaults()
            self._pending.clear()
            if self.on_saved:
                self.on_saved()
            self.destroy()
