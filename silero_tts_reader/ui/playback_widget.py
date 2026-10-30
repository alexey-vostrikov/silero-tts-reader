"""Плавающий виджет воспроизведения — поверх всех окон, в правом нижнем углу (GTK3).

Переписано с Qt на PyGObject: вместо сигналов — обычные callbacks-атрибуты
(``on_pause_resume``, ``on_stop``, ``on_speed_changed``, ``on_rewind``,
``on_seek_fraction``), вместо ``paintEvent`` — обработчик ``draw`` на cairo,
вместо ``QPropertyAnimation`` — пошаговый таймер GLib.
"""
from __future__ import annotations

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
        self.set_size_request(-1, 6)
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
        self._position_bottom_right()
        self.apply_theme(True, self._accent_color)

    # ── Окно ─────────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_position(Gtk.WindowPosition.NONE)
        self.set_size_request(WIDGET_WIDTH, WIDGET_HEIGHT)

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

        self._btn_pause = self._make_button("⏸", _("Пауза / Продолжить"), "playback-accent")
        self._btn_pause.connect("clicked", lambda _b: self._on_pause_resume_clicked())
        top_row.pack_start(self._btn_pause, False, False, 0)

        self._btn_forward = self._make_button("▶▶", _("Вперёд на 5 секунд"), "playback-neutral")
        self._btn_forward.connect("clicked", lambda _b: self._emit_rewind(5.0))
        top_row.pack_start(self._btn_forward, False, False, 0)

        self._btn_stop = self._make_button("⏹", _("Остановить"), "playback-danger")
        self._btn_stop.connect("clicked", lambda _b: self._on_stop_clicked())
        top_row.pack_start(self._btn_stop, False, False, 0)

        # ── Полоса прогресса ─────────────────────────────────────────────────
        self._progress = ProgressBar(self._on_timeline_clicked)
        self._progress.set_tooltip_text(_("Нажмите для перемотки"))
        box.pack_start(self._progress, False, False, 0)

        # ── Нижний ряд: скорость ─────────────────────────────────────────────
        bottom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(bottom_row, False, False, 0)

        self._speed_title = Gtk.Label(label=_("Скорость:"))
        self._speed_title.set_xalign(0.0)
        bottom_row.pack_start(self._speed_title, False, False, 0)

        # Шкала в реальных значениях скорости: 0.5×–3.0× шагом 0.25×
        self._speed_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 3.0, 0.25)
        self._speed_slider.set_draw_value(False)
        self._speed_slider.set_value(1.0)
        self._speed_slider.set_tooltip_text(_("Скорость воспроизведения"))
        self._speed_slider.set_sensitive(SPEED_AVAILABLE)
        self._speed_slider.connect("value-changed", self._on_speed_changed)
        bottom_row.pack_start(self._speed_slider, True, True, 0)

        self._speed_value = Gtk.Label(label="1.0×")
        self._speed_value.set_width_chars(4)
        bottom_row.pack_start(self._speed_value, False, False, 0)

        if not SPEED_AVAILABLE:
            no_rb = Gtk.Label(label=_("(rubberband не найден)"))
            no_rb.set_name("playback-warning")
            bottom_row.pack_start(no_rb, False, False, 0)

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
        button.set_size_request(30, 30)
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
            #playback-root label {{ color: {fg}; font-size: 12px; font-weight: 600; }}
            #playback-root #playback-warning {{ color: #f59e0b; font-size: 10px; font-weight: normal; }}
            .playback-neutral {{
                background-image: none;
                background-color: {neutral_bg};
                color: {fg};
                border: 1px solid {neutral_border};
                border-radius: 8px;
                font-size: 10px;
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
                font-size: 14px;
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
                font-size: 14px;
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
        self._speed_title.set_markup(f'<span foreground="{muted}" size="small">{_("Скорость:")}</span>')
        self._speed_value.set_markup(f'<span foreground="{fg}" size="small">{self._speed_slider.get_value():.2g}×</span>')

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

        cr.new_sub_path()
        cr.arc(width - CORNER_RADIUS, CORNER_RADIUS, CORNER_RADIUS, -1.5708, 0)
        cr.arc(width - CORNER_RADIUS, height - CORNER_RADIUS, CORNER_RADIUS, 0, 1.5708)
        cr.arc(CORNER_RADIUS, height - CORNER_RADIUS, CORNER_RADIUS, 1.5708, 3.1416)
        cr.arc(CORNER_RADIUS, CORNER_RADIUS, CORNER_RADIUS, 3.1416, 4.7124)
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

    def _position_bottom_right(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            return
        workarea = monitor.get_workarea()
        x = workarea.x + workarea.width - WIDGET_WIDTH - 16
        y = workarea.y + workarea.height - WIDGET_HEIGHT - 16
        self.move(x, y)

    def restore_position(self, x: int, y: int) -> None:
        if x < 0 or y < 0:
            self._position_bottom_right()
        else:
            self.move(x, y)

    def fade_in(self) -> None:
        self._cancel_hide_timer()
        self._stop_fade()
        self.set_opacity(1.0)
        self.set_keep_above(True)
        self.show_all()
        self.present()

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
        self._btn_pause.set_label("▶" if paused else "⏸")
        self._label.set_text(_("⏸ Пауза") if paused else _("🔊 Воспроизведение"))

    def set_speed(self, speed: float) -> None:
        clamped = max(0.5, min(3.0, round(float(speed) / 0.25) * 0.25))
        self._speed_slider.handler_block_by_func(self._on_speed_changed)
        self._speed_slider.set_value(clamped)
        self._speed_slider.handler_unblock_by_func(self._on_speed_changed)
        self._speed_value.set_text(f"{clamped:.2g}×")

    # ── Обработчики ──────────────────────────────────────────────────────────

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

    def _on_speed_changed(self, scale: Gtk.Scale) -> None:
        speed = round(scale.get_value() * 4) / 4  # кратно 0.25
        self._speed_value.set_text(f"{speed:.2g}×")
        if self.on_speed_changed:
            self.on_speed_changed(speed)

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
