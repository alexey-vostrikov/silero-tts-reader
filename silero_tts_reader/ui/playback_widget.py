"""Плавающий виджет воспроизведения — поверх всех окон, в правом нижнем углу (GTK3).

Переписано с Qt на PyGObject: вместо сигналов — обычные callbacks-атрибуты
(``on_pause_resume``, ``on_stop``, ``on_speed_changed``, ``on_rewind``,
``on_seek_fraction``), вместо ``paintEvent`` — обработчик ``draw`` на cairo,
вместо ``QPropertyAnimation`` — пошаговый таймер GLib.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..core.audio_player import SPEED_AVAILABLE
from ..i18n import _

WIDGET_WIDTH = 360
WIDGET_HEIGHT = 95
CORNER_RADIUS = 14


class ProgressBar(Gtk.DrawingArea):
    """Полоса прогресса, которая по клику сообщает долю 0.0–1.0.

    Своя отрисовка вместо ``Gtk.ProgressBar``: у GTK3 нельзя покрасить «заполнение»
    отдельным цветом без хаков с CSS-узлами, а здесь нужен акцент темы.
    """

    def __init__(self, on_seek) -> None:
        super().__init__()
        self._fraction = 0.0
        self._on_seek = on_seek
        self._track = "rgba(255, 255, 255, 0.15)"
        self._fill = "#6366f1"
        self.set_size_request(-1, 4)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        try:
            cursor = Gdk.Cursor.new_from_name(self.get_display(), "pointer")
            self.window and self.window.set_cursor(cursor)  # type: ignore[union-attr]
            self._cursor = cursor
        except Exception:
            self._cursor = None
        self.connect("button-press-event", self._on_button_press)

    def apply_theme(self, track: str, fill: str) -> None:
        self._track = track
        self._fill = fill
        self.queue_draw()

    def set_fraction(self, value: float) -> None:
        self._fraction = max(0.0, min(1.0, float(value)))
        self.queue_draw()

    def do_realize(self) -> None:  # noqa: N802 — имя хука GTK
        Gtk.DrawingArea.do_realize(self)
        if self._cursor is not None:
            self.get_window().set_cursor(self._cursor)

    def do_draw(self, cr) -> bool:  # noqa: N802
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        radius = height / 2

        cr.set_source_rgba(*self._rgba(self._track))
        self._rounded_rect(cr, 0, 0, width, height, radius)
        cr.fill()

        if self._fraction > 0:
            filled = max(radius * 2, width * self._fraction)
            self._rounded_rect(cr, 0, 0, filled, height, radius)
            cr.set_source_rgba(*self._rgba(self._fill))
            cr.fill()
        return False

    @staticmethod
    def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
        r = max(0.0, min(r, w / 2, h / 2))
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    @staticmethod
    def _rgba(spec: str) -> tuple[float, float, float, float]:
        """Строка ``#rrggbb`` или ``rgba(r, g, b, a)`` → кортеж для cairo."""
        spec = (spec or "").strip()
        if spec.startswith("rgba("):
            parts = spec[5:-1].split(",")
            r, g, b = (float(p) / 255 for p in parts[:3])
            a = float(parts[3]) if len(parts) > 3 else 1.0
            return (r, g, b, a)
        if spec.startswith("rgb("):
            parts = spec[4:-1].split(",")
            r, g, b = (float(p) / 255 for p in parts[:3])
            return (r, g, b, 1.0)
        value = spec.lstrip("#")
        if len(value) >= 6:
            return (
                int(value[0:2], 16) / 255,
                int(value[2:4], 16) / 255,
                int(value[4:6], 16) / 255,
                1.0,
            )
        return (0.39, 0.4, 0.95, 1.0)

    def _on_button_press(self, _widget, event) -> bool:
        if event.button == 1 and self.get_allocated_width() > 0:
            fraction = max(0.0, min(1.0, event.x / self.get_allocated_width()))
            self.set_fraction(fraction)
            if self._on_seek:
                self._on_seek(fraction)
            return True
        return False


APP_TITLE = "Silero TTS Reader"

# Глифы кнопок. ⏸ (U+23F8) и ⏹ (U+23F9) есть только в Unifont и FreeSerif:
# система подставляет их вместо Noto, и значки выходят заметно мельче, чем ◀▶
# у перемотки. Эти символы есть в Noto и рисуются в том же масштабе.
PAUSE_GLYPH = "▌▌"
PLAY_GLYPH = "▶"
STOP_GLYPH = "■"

# Выбор скорости: шаг 0,1× — этого достаточно, а список остаётся коротким.
SPEED_DEFAULT = 1.0
SPEED_CHOICES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


def _icon_file() -> str:
    """Иконка приложения: из установленного пакета или из репозитория."""
    candidates = (
        Path("/usr/share/icons/hicolor/256x256/apps/silero-tts-reader.png"),
        Path("/usr/share/icons/hicolor/128x128/apps/silero-tts-reader.png"),
        Path("/usr/share/pixmaps/silero-tts-reader.png"),
        Path(__file__).resolve().parent.parent.parent / "assets" / "icon.png",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


class PlaybackWidget(Gtk.Window):
    """Полупрозрачный виджет управления воспроизведением поверх других окон."""

    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        # Callbacks, которые проставляет приложение (замена pyqtSignal)
        self.on_pause_resume: Callable[[], None] | None = None
        self.on_stop: Callable[[], None] | None = None
        self.on_speed_changed: Callable[[float], None] | None = None
        self.on_rewind: Callable[[float], None] | None = None
        self.on_seek_fraction: Callable[[float], None] | None = None

        self._is_dark = True
        self._accent_color = "#6366f1"
        self._is_paused = False
        self._fade_source: int | None = None
        self._hide_timer: int | None = None

        self._setup_window()
        self._setup_ui()
        # Кнопки создаются в _setup_ui, поэтому размеры считаем ещё раз уже
        # по готовому интерфейсу — иначе они остаются исходными 30×30.
        self._apply_metrics()
        self._position_bottom_right()
        self.apply_theme(True, self._accent_color)

    # ── Окно ─────────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.set_decorated(False)
        self.set_resizable(False)
        # Окно должно находиться в панели задач и в переключателе окон: иначе
        # пользователь не видит, что приложение работает, и не может вернуться
        # к нему после переключения на другое.
        self.set_skip_taskbar_hint(False)
        self.set_skip_pager_hint(False)
        self.set_accept_focus(True)
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.set_title(APP_TITLE)
        # Иконка по имени из темы: менеджер окон сопоставляет её с .desktop и
        # показывает нужный значок. Файл напрямую не годится — вместо иконки
        # приложения тогда рисуется стандартная «шестерёнка» GTK.
        self.set_wmclass("silero-tts-reader", "Silero TTS Reader")
        self.set_icon_name("silero-tts-reader")
        icon = _icon_file()
        if icon:
            try:
                self.set_icon_from_file(icon)
            except GLib.Error as exc:
                # i18n: нет перевода — строка журнала в консоли
                print(f"  Иконка из файла не загрузилась ({icon}): {exc}", flush=True)
        self.set_position(Gtk.WindowPosition.NONE)
        # Размер зависит от монитора и его масштаба: при жёстких 360×95 виджет
        # на HiDPI выглядел крошечным, а на широком экране — несоразмерно узким.
        self._metrics = {"width": WIDGET_WIDTH, "height": WIDGET_HEIGHT,
                         "button": 30, "corner": CORNER_RADIUS}
        self._apply_metrics()
        self.connect("notify::scale-factor", lambda *_: self._apply_metrics())
        self.connect("screen-changed", lambda *_: self._apply_metrics())

        # Прозрачный фон окна: сам фон рисуем на cairo, чтобы получить скругления.
        screen = self.get_screen()
        visual = screen.get_rgba_visual() if screen else None
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        self.set_app_paintable(True)

        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self._css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.set_name("playback-root")

        # Перетаскивание за свободное место окна
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._on_background_press)
        self.connect("draw", self._on_draw)

    def _setup_ui(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.add(box)

        # ── Верхний ряд: заголовок и кнопки ──────────────────────────────────
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(top_row, False, False, 0)
        self._top_row = top_row

        self._label = Gtk.Label(label=_("🔊 Воспроизведение"))
        self._label.set_xalign(0.0)
        top_row.pack_start(self._label, False, False, 0)
        top_row.pack_start(Gtk.Box(), True, True, 0)  # растяжка

        self._btn_rewind = self._make_button("◀◀", _("Назад на 5 секунд"), "playback-neutral")
        self._btn_rewind.connect("clicked", lambda _b: self._emit_rewind(-5.0))
        top_row.pack_start(self._btn_rewind, False, False, 0)

        self._btn_pause = self._make_button(PAUSE_GLYPH, _("Пауза / Продолжить"), "playback-accent")
        self._btn_pause.connect("clicked", lambda _b: self._on_pause_resume_clicked())
        top_row.pack_start(self._btn_pause, False, False, 0)

        self._btn_forward = self._make_button("▶▶", _("Вперёд на 5 секунд"), "playback-neutral")
        self._btn_forward.connect("clicked", lambda _b: self._emit_rewind(5.0))
        top_row.pack_start(self._btn_forward, False, False, 0)

        self._btn_stop = self._make_button(STOP_GLYPH, _("Остановить"), "playback-danger")
        self._btn_stop.connect("clicked", lambda _b: self._on_stop_clicked())
        top_row.pack_start(self._btn_stop, False, False, 0)

        # ── Полоса прогресса и скорость справа от неё ────────────────────────
        # Скорость — выпадающий список, а не слайдер: значения дискретные, и
        # список показывает их сразу, без «поймай нужную четверть» мышью.
        progress_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(progress_row, False, False, 0)

        self._progress = ProgressBar(self._on_timeline_clicked)
        self._progress.set_tooltip_text(_("Нажмите для перемотки"))
        progress_row.pack_start(self._progress, True, True, 0)

        self._speed_combo = Gtk.ComboBoxText()
        for choice in SPEED_CHOICES:
            self._speed_combo.append(str(choice), f"{choice:.2g}×")
        self._speed_combo.set_active_id(str(SPEED_DEFAULT))
        self._speed_combo.set_tooltip_text(
            _("Скорость воспроизведения") if SPEED_AVAILABLE
            else _("Скорость недоступна: не найден rubberband")
        )
        self._speed_combo.set_sensitive(SPEED_AVAILABLE)
        self._speed_combo.set_valign(Gtk.Align.CENTER)
        self._speed_combo.connect("changed", self._on_speed_selected)
        progress_row.pack_start(self._speed_combo, False, False, 0)

    def set_menu_model(self, model) -> None:
        """Кнопка меню в виджете: те же действия, что и у клавиш.

        Модель приходит из приложения (``app.*``), поэтому в окне не нужен
        собственный набор действий — достаточно, чтобы окно было привязано к
        приложению (``Gtk.Application.add_window``).
        """
        if model is None:
            return
        button = Gtk.MenuButton()
        button.set_menu_model(model)
        button.set_tooltip_text(_("Меню"))
        button.set_size_request(self._metrics.get("button", 30),
                               self._metrics.get("button", 30))
        # GTK3: класс ставится через StyleContext (add_css_class — это уже GTK4)
        button.get_style_context().add_class("playback-neutral")
        self._top_row.pack_start(button, False, False, 0)
        button.show()
        self._menu_button = button

    def _make_button(self, label: str, tooltip: str, css_class: str) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.set_size_request(30, 30)
        btn.set_tooltip_text(tooltip)
        btn.get_style_context().add_class(css_class)
        return btn

    # ── Тема ─────────────────────────────────────────────────────────────────

    def apply_theme(self, is_dark: bool = True, accent_color: str = "#6366f1") -> None:
        self._is_dark = is_dark
        self._accent_color = accent_color
        self._last_theme = (is_dark, accent_color)
        font_label = self._metrics.get("font_label", 13)
        font_button = self._metrics.get("font_button", 12)
        font_small = self._metrics.get("font_small", 11)

        if is_dark:
            fg, muted = "#e2e8f0", "#94a3b8"
            track = "rgba(255, 255, 255, 0.15)"
            neutral_bg = "rgba(255, 255, 255, 0.12)"
            neutral_hover = "rgba(255, 255, 255, 0.22)"
            neutral_active = "rgba(255, 255, 255, 0.32)"
            neutral_border = "rgba(255, 255, 255, 0.15)"
            hover_accent = _lighten(accent_color, 1.2)
        else:
            fg, muted = "#0f172a", "#64748b"
            track = "rgba(0, 0, 0, 0.1)"
            neutral_bg = "rgba(0, 0, 0, 0.06)"
            neutral_hover = "rgba(0, 0, 0, 0.12)"
            neutral_active = "rgba(0, 0, 0, 0.2)"
            neutral_border = "rgba(0, 0, 0, 0.12)"
            hover_accent = _lighten(accent_color, 0.87)

        self._css.load_from_data(
            f"""
            #playback-root {{ background-color: transparent; }}
            #playback-root label {{ color: {fg}; font-size: {font_label}px; font-weight: 600; }}
            #playback-root #playback-warning {{ color: #f59e0b; font-size: {font_small}px; font-weight: normal; }}
            .playback-neutral {{
                background-image: none;
                background-color: {neutral_bg};
                color: {fg};
                border: 1px solid {neutral_border};
                border-radius: 8px;
                font-size: {font_button}px;
                font-weight: bold;
                padding: 0;
                text-shadow: none;
                box-shadow: none;
            }}
            .playback-neutral:hover {{
                background-color: {neutral_hover};
                color: {fg};
            }}
            .playback-neutral:active {{
                background-color: {neutral_active};
            }}
            .playback-accent {{
                background-image: none;
                background-color: {neutral_bg};
                color: {fg};
                border: 1px solid {neutral_border};
                border-radius: 8px;
                font-size: {font_label}px;
                padding: 0;
                text-shadow: none;
                box-shadow: none;
            }}
            .playback-accent:hover {{
                background-color: {accent_color};
                border-color: {accent_color};
                color: #ffffff;
            }}
            .playback-accent:active {{
                background-color: {hover_accent};
                color: #ffffff;
            }}
            .playback-danger {{
                background-image: none;
                background-color: {neutral_bg};
                color: {fg};
                border: 1px solid {neutral_border};
                border-radius: 8px;
                font-size: {font_label + 8}px;
                padding: 0;
                text-shadow: none;
                box-shadow: none;
            }}
            .playback-danger:hover {{
                background-color: #ef4444;
                border-color: #ef4444;
                color: #ffffff;
            }}
            .playback-danger:active {{ background-color: #dc2626; color: #ffffff; }}
            scale trough {{
                background-color: {track};
                border-radius: 2px;
                min-height: 4px;
            }}
            scale highlight {{
                background-color: {accent_color};
                border-radius: 2px;
            }}
            scale slider {{
                background-color: {accent_color};
                border: none;
                border-radius: 6px;
                min-width: 12px;
                min-height: 12px;
                margin: -4px 0;
            }}
            """.encode("utf-8")
        )

        # Цвет берётся из fg, поэтому ветки одинаковы — раньше во второй
        # оставалась непереведённая строка.
        self._label.set_markup(
            f'<span foreground="{fg}" size="small" weight="bold">{_("🔊 Воспроизведение")}</span>'
        )

        self._progress.apply_theme(track, accent_color)
        if self._is_paused:
            self.set_paused(True)
        self.queue_draw()

    # ── Отрисовка фона окна ──────────────────────────────────────────────────

    def _on_draw(self, _widget, cr) -> bool:
        """Нарисовать скруглённый фон ПОД содержимым.

        Именно сигнал ``draw``: у Gtk.Window хук ``do_draw`` и есть отрисовка
        детей, и переопределение его своими руками оставляло окно пустым —
        фон рисовался, а метки и кнопки пропадали.
        """
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        corner = self._metrics.get("corner", CORNER_RADIUS)

        cr.new_sub_path()
        cr.arc(width - corner, corner, corner, -1.5708, 0)
        cr.arc(width - corner, height - corner, corner, 0, 1.5708)
        cr.arc(corner, height - corner, corner, 1.5708, 3.1416)
        cr.arc(corner, corner, corner, 3.1416, 4.7124)
        cr.close_path()

        if self._is_dark:
            cr.set_source_rgba(24 / 255, 24 / 255, 27 / 255, 1.0)
        else:
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        cr.fill_preserve()
        if self._is_dark:
            cr.set_source_rgba(1, 1, 1, 0.1)
        else:
            cr.set_source_rgba(0, 0, 0, 0.16)
        cr.set_line_width(1.0)
        cr.stroke()
        return False  # дети рисуются поверх

    # ── Позиция и показ ──────────────────────────────────────────────────────

    def _monitor(self):
        """Монитор, на котором находится окно (иначе основной)."""
        display = self.get_display() or Gdk.Display.get_default()
        if display is None:
            return None
        window = self.get_window()
        if window is not None:
            monitor = display.get_monitor_at_window(window)
            if monitor is not None:
                return monitor
        return display.get_primary_monitor() or display.get_monitor(0)

    def _apply_metrics(self) -> None:
        """Подогнать размеры виджета под монитор и его масштаб.

        Ширина — доля рабочей области (в логических пикселях), поэтому виджет
        одинаково читается и на ноутбучном экране, и на 4K с масштабом 200 %:
        GTK сам переводит логические пиксели в физические.
        """
        monitor = self._monitor()
        if monitor is None:
            return
        workarea = monitor.get_workarea()
        scale = max(1, monitor.get_scale_factor())

        base = int(workarea.width * 0.26)
        # Пределы: не мельче исходного и не шире трети экрана
        width = max(WIDGET_WIDTH, min(base, int(workarea.width * 0.34), 900))
        height = int(width * WIDGET_HEIGHT / WIDGET_WIDTH)
        button = max(30, int(round(30 * width / WIDGET_WIDTH)))
        corner = max(CORNER_RADIUS, int(round(CORNER_RADIUS * width / WIDGET_WIDTH)))

        # Текст и полоса прогресса крупнеют вместе с виджетом: иначе на широком
        # экране надписи выглядят мелкими, а полоса — толстой.
        ratio = width / WIDGET_WIDTH
        font_label = max(12, min(22, round(13 * ratio ** 0.6)))
        font_button = max(10, min(20, round(12 * ratio ** 0.6)))
        font_small = max(10, min(18, round(11 * ratio ** 0.6)))
        # Полоса тонкая намеренно: это индикатор места в книге, а не
        # самостоятельный элемент управления.
        progress = max(3, min(4, round(4 * ratio ** 0.4)))

        changed = self._metrics != {"width": width, "height": height,
                                    "button": button, "corner": corner}
        self._metrics = {"width": width, "height": height,
                         "button": button, "corner": corner, "scale": scale,
                         "font_label": font_label, "font_button": font_button,
                         "font_small": font_small, "progress": progress}
        self.set_size_request(width, height)

        for button_widget in (getattr(self, "_btn_rewind", None),
                              getattr(self, "_btn_pause", None),
                              getattr(self, "_btn_forward", None),
                              getattr(self, "_btn_stop", None)):
            if button_widget is not None:
                button_widget.set_size_request(button, button)

        progress_bar = getattr(self, "_progress", None)
        if progress_bar is not None:
            progress_bar.set_size_request(-1, progress)

        if changed:
            self.queue_draw()
            # Тема описана в CSS фиксированными размерами — переписываем её
            # под новые метрики, иначе шрифты остаются прежними.
            last_theme = getattr(self, "_last_theme", None)
            if last_theme is not None:
                self.apply_theme(*last_theme)

    def _position_bottom_right(self) -> None:
        monitor = self._monitor()
        if monitor is None:
            return
        workarea = monitor.get_workarea()
        width = self._metrics.get("width", WIDGET_WIDTH)
        height = self._metrics.get("height", WIDGET_HEIGHT)
        x = workarea.x + workarea.width - width - 16
        y = workarea.y + workarea.height - height - 16
        self.move(x, y)

    def restore_position(self, x: int, y: int) -> None:
        if x < 0 or y < 0:
            self._position_bottom_right()
        else:
            self.move(x, y)

    def fade_in(self) -> None:
        """Показать окно, не блокируя главный поток.

        `show_all()` и `present()` на XWayland могут ждать ответа сервера, а
        `set_keep_above` там же вешает окно: в дампе стеков главный поток стоял
        внутри fade_in, и приложение не показывало окно, пока шёл синтез.
        Поэтому показ откладывается на ближайший оборот цикла GLib.
        """
        self._cancel_hide_timer()
        self._stop_fade()
        GLib.idle_add(self._show_now)

    def _show_now(self) -> bool:
        self.set_opacity(1.0)
        self.show_all()
        self.present()
        # Позицию задаём после показа: до map менеджер окон её игнорирует, и
        # виджет оказывался в левом верхнем углу вместо правого нижнего.
        self._position_bottom_right()
        # Один раз в журнал: видно ли окно на самом деле. Без этой строки
        # «окно не появилось» приходится угадывать — а с ней видно и размер,
        # и позицию, и то, что окно отображено.
        # i18n: нет перевода — строка журнала в консоли
        print(
            f"  Окно показано: {self.get_allocated_width()}×{self.get_allocated_height()}"
            f", позиция {self.get_position()}, видимо {self.get_visible()}"
            f", отображено {self.get_mapped()}",
            flush=True,
        )
        return False   # одноразовый idle

    def schedule_hide(self, delay_ms: int = 2000) -> None:
        self._cancel_hide_timer()
        self._hide_timer = GLib.timeout_add(max(1, int(delay_ms)), self._begin_fade_out)

    def _begin_fade_out(self) -> bool:
        self._hide_timer = None
        self._stop_fade()
        self._fade_source = GLib.timeout_add(16, self._fade_step, 1.0)
        return False  # одноразовый таймер

    def _fade_step(self, opacity: float) -> bool:
        opacity -= 0.08
        if opacity <= 0.0:
            self.set_opacity(0.0)
            self._fade_source = None
            self.hide()
            return False
        self.set_opacity(opacity)
        self._fade_source = GLib.timeout_add(16, self._fade_step, opacity)
        return False

    def _stop_fade(self) -> None:
        if self._fade_source is not None:
            GLib.source_remove(self._fade_source)
            self._fade_source = None

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer is not None:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = None

    # ── Публичные слоты ──────────────────────────────────────────────────────

    def set_progress(self, value: float) -> None:
        self._progress.set_fraction(value)

    def set_paused(self, paused: bool) -> None:
        self._is_paused = paused
        self._btn_pause.set_label(PLAY_GLYPH if paused else PAUSE_GLYPH)
        self._label.set_text(_("Пауза") if paused else _("🔊 Воспроизведение"))

    def set_speed(self, speed: float) -> None:
        """Показать скорость в списке, не вызывая обработчик изменения."""
        clamped = max(SPEED_CHOICES[0], min(SPEED_CHOICES[-1], float(speed)))
        # Ближайшее значение из списка: конфиг мог хранить 1,37 после слайдера
        nearest = min(SPEED_CHOICES, key=lambda value: abs(value - clamped))
        self._speed_combo.handler_block_by_func(self._on_speed_selected)
        self._speed_combo.set_active_id(str(nearest))
        self._speed_combo.handler_unblock_by_func(self._on_speed_selected)

    def _on_pause_resume_clicked(self) -> None:
        if self.on_pause_resume:
            self.on_pause_resume()

    def _on_stop_clicked(self) -> None:
        self.schedule_hide(150)
        if self.on_stop:
            self.on_stop()

    def _emit_rewind(self, seconds: float) -> None:
        if self.on_rewind:
            self.on_rewind(seconds)

    def _on_timeline_clicked(self, fraction: float) -> None:
        self.set_progress(fraction)
        if self.on_seek_fraction:
            self.on_seek_fraction(fraction)

    def _on_speed_selected(self, combo: Gtk.ComboBoxText) -> None:
        """Скорость выбрана в списке."""
        active = combo.get_active_id()
        if not active:
            return
        if self.on_speed_changed:
            self.on_speed_changed(float(active))

    def _on_background_press(self, _widget, event) -> bool:
        """Перетаскивание окна за фон: дети события не отдают, поэтому только пустые места."""
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    # Наведение мышью отменяет автоскрытие, как в Qt-версии
    def do_enter_notify_event(self, _event) -> bool:  # noqa: N802
        self._cancel_hide_timer()
        self._stop_fade()
        self.set_opacity(1.0)
        return False


def _lighten(color: str, factor: float) -> str:
    from .theme_manager import shift_color

    return shift_color(color, factor)
