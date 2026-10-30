"""Плавающий виджет воспроизведения — поверх всех окон, в правом нижнем углу (GTK4).

Версия на GTK4. Что отличается от GTK3-варианта, и почему:

* фон окна рисует CSS (``border-radius`` у контейнера), а не cairo в сигнале
  ``draw``: у GTK4 окно не рисуют, а собирают из виджетов;
* ``pack_start`` заменён на ``append`` с ``hexpand``: раскладка в GTK4 без
  «упаковщиков»;
* события мыши приходят контроллерами (``Gtk.GestureClick``,
  ``Gtk.EventControllerMotion``), а не сигналами ``button-press-event``;
* ``move()`` у окна больше нет, поэтому угол задаёт ``ui/layer_shell``
  (протокол layer-shell, а на X11 — запрос к X-серверу);
* прозрачность меняется через CSS: ``Gtk.Widget.set_opacity`` удалён.
"""
from __future__ import annotations

import math
from collections.abc import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..core.audio_player import SPEED_AVAILABLE  # noqa: E402
from ..i18n import _  # noqa: E402
from . import layer_shell  # noqa: E402
from .window_icon import apply_icon  # noqa: E402

WIDGET_WIDTH = 360
WIDGET_HEIGHT = 95
CORNER_RADIUS = 14

APP_TITLE = "Silero TTS Reader"

# Глифы кнопок. ⏸ (U+23F8) и ⏹ (U+23F9) есть только в Unifont и FreeSerif:
# система подставляет их вместо Noto, и значки выходят заметно мельче, чем ◀▶
# у перемотки. Эти символы есть в Noto и рисуются в том же масштабе.
# Папка из Noto Emoji: такой глиф узнаётся сразу и не требует подписи.
OPEN_GLYPH = "📂"
PAUSE_GLYPH = "▌▌"
PLAY_GLYPH = "▶"
STOP_GLYPH = "■"

# Выбор скорости: шаг 0,1× — этого достаточно, а список остаётся коротким.
SPEED_DEFAULT = 1.0
SPEED_CHOICES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


def speed_label(value: float) -> str:
    """Подпись скорости: «1×», «1.25×» — без лишнего нуля."""
    return f"{value:.3g}×"


class ProgressBar(Gtk.DrawingArea):
    """Полоса прогресса, которая по клику сообщает долю 0.0–1.0.

    Своя отрисовка вместо ``Gtk.ProgressBar``: у него нельзя покрасить
    «заполнение» отдельным цветом без хаков с CSS-узлами, а здесь нужен
    акцент темы.
    """

    def __init__(self, on_seek) -> None:
        super().__init__()
        self._fraction = 0.0
        self._on_seek = on_seek
        self._track = "rgba(255, 255, 255, 0.15)"
        self._fill = "#6366f1"
        self.set_size_request(-1, 4)
        self.set_draw_func(self._draw)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)

    def apply_theme(self, track: str, fill: str) -> None:
        self._track = track
        self._fill = fill
        self.queue_draw()

    def set_fraction(self, value: float) -> None:
        self._fraction = max(0.0, min(1.0, float(value)))
        self.queue_draw()

    def _draw(self, _area, cr, width: int, height: int) -> None:
        radius = height / 2

        cr.set_source_rgba(*self._rgba(self._track))
        self._rounded_rect(cr, 0, 0, width, height, radius)
        cr.fill()

        if self._fraction > 0:
            filled = max(radius * 2, width * self._fraction)
            self._rounded_rect(cr, 0, 0, filled, height, radius)
            cr.set_source_rgba(*self._rgba(self._fill))
            cr.fill()

    @staticmethod
    def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
        r = max(0.0, min(r, w / 2, h / 2))
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
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

    def _on_pressed(self, gesture, _n_press, x: float, _y: float) -> None:
        if gesture.get_current_button() != 1:
            return
        width = self.get_width()
        if width <= 0:
            return
        fraction = max(0.0, min(1.0, x / width))
        self.set_fraction(fraction)
        if self._on_seek:
            self._on_seek(fraction)


class PlaybackWidget(Gtk.ApplicationWindow):
    """Полупрозрачный виджет управления воспроизведением поверх других окон."""

    def __init__(self, application=None, monitor_index: int | None = None) -> None:
        super().__init__(application=application)

        # Callbacks, которые проставляет приложение
        self.on_open_file: Callable[[], None] | None = None
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
        # Как окно поставлено: "layer" (протокол), "x11" (запрос к серверу)
        # или "none" (место выбрал композитор). Для последнего наш move() не
        # действует вовсе.
        self._position_mode = "none"
        self._monitor_index = monitor_index

        self._metrics = {
            "width": WIDGET_WIDTH, "height": WIDGET_HEIGHT,
            "button": 30, "corner": CORNER_RADIUS,
            "font_label": 13, "font_button": 12, "font_small": 11, "progress": 4,
        }
        self._last_theme: tuple[bool, str] | None = None

        self._setup_window()
        self._setup_ui()
        self._apply_metrics()
        self.apply_theme(True, self._accent_color)
        # Угол задаём ДО показа: протокол переоформляет поверхность при её
        # создании, для уже показанного окна это поздно.
        self._place_window()

    # ── Окно ─────────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_title(APP_TITLE)
        # Значок — общим кодом: описание в ui/window_icon.py.
        apply_icon(self)
        self.set_default_size(WIDGET_WIDTH, WIDGET_HEIGHT)

        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        # Прозрачность меняется отдельным провайдером: set_opacity в GTK4 нет.
        self._opacity_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._opacity_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

        self.connect("notify::scale-factor", lambda *_: self._apply_metrics())

    def _setup_ui(self) -> None:
        # Фон рисует CSS: border-radius у контейнера даёт скругления окна.
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._root.add_css_class("playback-root")
        self._root.set_margin_top(10)
        self._root.set_margin_bottom(10)
        self._root.set_margin_start(12)
        self._root.set_margin_end(12)
        self.set_child(self._root)

        # ── Верхний ряд: заголовок и кнопки ──────────────────────────────────
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._root.append(top_row)
        self._top_row = top_row

        self._label = Gtk.Label(label=_("🔊 Воспроизведение"))
        self._label.set_xalign(0.0)
        top_row.append(self._label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_row.append(spacer)

        # Кнопка открытия файла: раньше файл можно было дать только аргументом
        # командной строки или через «Открыть с помощью».
        self._btn_open = self._make_button(OPEN_GLYPH, _("Открыть файл…"),
                                           "playback-neutral")
        self._btn_open.connect("clicked", lambda _b: self._on_open_clicked())
        top_row.append(self._btn_open)

        self._btn_rewind = self._make_button("◀◀", _("Назад на 5 секунд"),
                                             "playback-neutral")
        self._btn_rewind.connect("clicked", lambda _b: self._emit_rewind(-5.0))
        top_row.append(self._btn_rewind)

        self._btn_pause = self._make_button(PAUSE_GLYPH, _("Пауза / Продолжить"),
                                            "playback-accent")
        self._btn_pause.connect("clicked", lambda _b: self._on_pause_resume_clicked())
        top_row.append(self._btn_pause)

        self._btn_forward = self._make_button("▶▶", _("Вперёд на 5 секунд"),
                                              "playback-neutral")
        self._btn_forward.connect("clicked", lambda _b: self._emit_rewind(5.0))
        top_row.append(self._btn_forward)

        self._btn_stop = self._make_button(STOP_GLYPH, _("Остановить"),
                                           "playback-danger")
        self._btn_stop.connect("clicked", lambda _b: self._on_stop_clicked())
        top_row.append(self._btn_stop)

        # ── Полоса прогресса и скорость справа от неё ────────────────────────
        progress_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._root.append(progress_row)

        self._progress = ProgressBar(self._on_timeline_clicked)
        self._progress.set_tooltip_text(_("Нажмите для перемотки"))
        self._progress.set_hexpand(True)
        progress_row.append(self._progress)

        # Скорость — выпадающий список, а не слайдер: значения дискретные, и
        # список показывает их сразу, без «поймай нужную четверть» мышью.
        # В GTK4 ComboBoxText устарел, вместо него DropDown со списком строк.
        self._speed_combo = Gtk.DropDown.new_from_strings(
            [speed_label(choice) for choice in SPEED_CHOICES])
        self._speed_combo.set_selected(SPEED_CHOICES.index(SPEED_DEFAULT))
        self._speed_combo.set_tooltip_text(
            _("Скорость воспроизведения") if SPEED_AVAILABLE
            else _("Скорость недоступна: не найден rubberband")
        )
        self._speed_combo.set_sensitive(SPEED_AVAILABLE)
        self._speed_combo.set_valign(Gtk.Align.CENTER)
        self._speed_combo.connect("notify::selected", self._on_speed_selected)
        progress_row.append(self._speed_combo)

        # Перетаскивание за свободное место окна и отмена автоскрытия при
        # наведении: в GTK4 это контроллеры, а не сигналы окна.
        drag = Gtk.GestureClick()
        drag.connect("pressed", self._on_background_press)
        self._root.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_enter)
        self._root.add_controller(motion)

    def set_menu_model(self, model) -> None:
        """Кнопка меню в виджете: те же действия, что и у клавиш.

        Модель приходит из приложения (``app.*``), поэтому в окне не нужен
        собственный набор действий — достаточно, чтобы окно было привязано к
        приложению.
        """
        if model is None:
            return
        button = Gtk.MenuButton()
        button.set_menu_model(model)
        button.set_tooltip_text(_("Меню"))
        button.set_size_request(self._metrics.get("button", 30),
                                self._metrics.get("button", 30))
        button.add_css_class("playback-neutral")
        self._top_row.append(button)
        self._menu_button = button

    def _make_button(self, label: str, tooltip: str, css_class: str) -> Gtk.Button:
        btn = Gtk.Button(label=label)
        btn.set_size_request(30, 30)
        btn.set_tooltip_text(tooltip)
        btn.add_css_class(css_class)
        return btn

    # ── Тема ─────────────────────────────────────────────────────────────────

    def apply_theme(self, is_dark: bool = True, accent_color: str = "#6366f1") -> None:
        self._is_dark = is_dark
        self._accent_color = accent_color
        self._last_theme = (is_dark, accent_color)
        font_label = self._metrics.get("font_label", 13)
        font_button = self._metrics.get("font_button", 12)
        # Глиф паузы (▌▌) заметно крупнее стрелок при том же кегле: два
        # закрашенных бруска выглядят тяжелее контурных треугольников. Поэтому
        # для него отдельный кегль — на 15 % меньше основного.
        font_pause = max(9, round(font_label * 0.85))
        font_small = self._metrics.get("font_small", 11)

        if is_dark:
            fg = "#e2e8f0"
            track = "rgba(255, 255, 255, 0.15)"
            background = "rgba(24, 24, 27, 0.97)"
            border = "rgba(255, 255, 255, 0.10)"
            neutral_bg = "rgba(255, 255, 255, 0.12)"
            neutral_hover = "rgba(255, 255, 255, 0.22)"
            neutral_active = "rgba(255, 255, 255, 0.32)"
            neutral_border = "rgba(255, 255, 255, 0.15)"
            hover_accent = _lighten(accent_color, 1.2)
        else:
            fg = "#0f172a"
            track = "rgba(0, 0, 0, 0.1)"
            background = "rgba(255, 255, 255, 0.97)"
            border = "rgba(0, 0, 0, 0.16)"
            neutral_bg = "rgba(0, 0, 0, 0.06)"
            neutral_hover = "rgba(0, 0, 0, 0.12)"
            neutral_active = "rgba(0, 0, 0, 0.2)"
            neutral_border = "rgba(0, 0, 0, 0.12)"
            hover_accent = _lighten(accent_color, 0.87)

        corner = self._metrics.get("corner", CORNER_RADIUS)
        self._css.load_from_data(
            f"""
            window {{
                background-color: transparent;
            }}
            .playback-root {{
                background-color: {background};
                border: 1px solid {border};
                border-radius: {corner}px;
            }}
            .playback-root label {{
                color: {fg};
                font-size: {font_label}px;
                font-weight: 600;
            }}
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
                font-size: {font_pause}px;
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
            dropdown {{
                background-image: none;
                background-color: {neutral_bg};
                color: {fg};
                border: 1px solid {neutral_border};
                border-radius: 8px;
                font-size: {font_small}px;
                min-width: 52px;
            }}
            dropdown > button {{
                background-image: none;
                background-color: transparent;
                border: none;
                box-shadow: none;
                padding: 0 4px;
            }}
            """.encode()
        )
        self._label.set_markup(
            f'<span foreground="{fg}" size="small" weight="bold">'
            f'{_("🔊 Воспроизведение")}</span>'
        )
        self._progress.apply_theme(track, accent_color)
        if self._is_paused:
            self.set_paused(True)
        self._set_opacity(1.0)

    # ── Позиция и показ ──────────────────────────────────────────────────────

    def _monitor(self):
        """Монитор, на котором находится окно (иначе основной)."""
        display = self.get_display() or Gdk.Display.get_default()
        if display is None:
            return None
        surface = self.get_surface()
        if surface is not None:
            monitor = display.get_monitor_at_surface(surface)
            if monitor is not None:
                return monitor
        # В GDK4 «основного» монитора нет — берём первый.
        return layer_shell.monitor_at(self, 0)

    def _apply_metrics(self) -> None:
        """Подогнать размеры виджета под монитор и его масштаб.

        Ширина — доля рабочей области (в логических пикселях), поэтому виджет
        одинаково читается и на ноутбучном экране, и на 4K с масштабом 200 %:
        GTK сам переводит логические пиксели в физические.
        """
        monitor = self._monitor()
        if monitor is None:
            return
        area = monitor.get_geometry()
        scale = max(1, monitor.get_scale_factor())

        base = int(area.width * 0.26)
        # Пределы: не мельче исходного и не шире трети экрана
        width = max(WIDGET_WIDTH, min(base, int(area.width * 0.34), 900))
        height = int(width * WIDGET_HEIGHT / WIDGET_WIDTH)
        button = max(30, round(30 * width / WIDGET_WIDTH))
        corner = max(CORNER_RADIUS, round(CORNER_RADIUS * width / WIDGET_WIDTH))

        # Текст и полоса прогресса крупнеют вместе с виджетом: иначе на широком
        # экране надписи выглядят мелкими, а полоса — толстой.
        ratio = width / WIDGET_WIDTH
        font_label = max(12, min(22, round(13 * ratio ** 0.6)))
        font_button = max(10, min(20, round(12 * ratio ** 0.6)))
        font_small = max(10, min(18, round(11 * ratio ** 0.6)))
        # Полоса тонкая намеренно: это индикатор места в книге, а не
        # самостоятельный элемент управления.
        progress = max(3, min(4, round(4 * ratio ** 0.4)))

        changed = self._metrics.get("width") != width
        self._metrics.update({
            "width": width, "height": height, "button": button,
            "corner": corner, "scale": scale, "font_label": font_label,
            "font_button": font_button, "font_small": font_small,
            "progress": progress,
        })
        self.set_size_request(width, height)

        for button_widget in (getattr(self, "_btn_open", None),
                              getattr(self, "_btn_rewind", None),
                              getattr(self, "_btn_pause", None),
                              getattr(self, "_btn_forward", None),
                              getattr(self, "_btn_stop", None)):
            if button_widget is not None:
                button_widget.set_size_request(button, button)

        progress_bar = getattr(self, "_progress", None)
        if progress_bar is not None:
            progress_bar.set_size_request(-1, progress)

        if changed and self._last_theme is not None:
            # Тема описана в CSS фиксированными размерами — переписываем её
            # под новые метрики, иначе шрифты остаются прежними.
            self.apply_theme(*self._last_theme)

    def _place_window(self) -> None:
        """Поставить окно в правый нижний угол доступным способом."""
        width = self._metrics.get("width", WIDGET_WIDTH)
        height = self._metrics.get("height", WIDGET_HEIGHT)
        self._position_mode = layer_shell.place_bottom_right(
            self, width, height, margin=16, monitor_index=self._monitor_index)

    def restore_position(self, x: int, y: int) -> None:
        """Вернуть окно на сохранённое место (только если это возможно).

        Под layer-shell место задаёт композитор, а под Wayland без протокола
        двигать окно нельзя вовсе — тогда вызов честно ничего не делает.
        """
        if self._position_mode == "layer":
            return
        if x < 0 or y < 0:
            self._place_window()
            return
        if self._position_mode == "x11":
            layer_shell.move_x11(self, x, y)

    def fade_in(self) -> None:
        """Показать окно, не блокируя главный поток.

        Показ откладывается на ближайший оборот цикла GLib: ``present()`` на
        XWayland может ждать ответа сервера, и приложение не показывало окно,
        пока шёл синтез.
        """
        self._cancel_hide_timer()
        self._stop_fade()
        GLib.idle_add(self._show_now)

    def _show_now(self) -> bool:
        self._set_opacity(1.0)
        self.present()
        # Позицию задаём после показа: до map менеджер окон её игнорирует.
        if self._position_mode != "layer":
            self._place_window()
        # Один раз в журнал: видно ли окно на самом деле. Без этой строки
        # «окно не появилось» приходится угадывать.
        print(
            f"  Окно показано: {self.get_width()}×{self.get_height()}"
            f", позиция {self._position_mode}, видимо {self.get_visible()}",
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
            self._set_opacity(0.0)
            self._fade_source = None
            self.set_visible(False)
            return False
        self._set_opacity(opacity)
        self._fade_source = GLib.timeout_add(16, self._fade_step, opacity)
        return False

    def _set_opacity(self, value: float) -> None:
        """Прозрачность окна через CSS: ``set_opacity`` в GTK4 удалён."""
        self._opacity = max(0.0, min(1.0, float(value)))
        self._opacity_css.load_from_data(
            f".playback-root {{ opacity: {self._opacity:.2f}; }}".encode())

    def get_opacity(self) -> float:
        """Текущая прозрачность — для проверок и журнала."""
        return getattr(self, "_opacity", 1.0)

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
        self._speed_combo.set_selected(SPEED_CHOICES.index(nearest))
        self._speed_combo.handler_unblock_by_func(self._on_speed_selected)

    def get_speed(self) -> float:
        """Выбранная скорость — для проверок."""
        selected = self._speed_combo.get_selected()
        if 0 <= selected < len(SPEED_CHOICES):
            return SPEED_CHOICES[selected]
        return SPEED_DEFAULT

    # ── Обработчики ──────────────────────────────────────────────────────────

    def _on_open_clicked(self) -> None:
        if self.on_open_file is not None:
            self.on_open_file()

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

    def _on_speed_selected(self, combo, _param=None) -> None:
        """Скорость выбрана в списке."""
        if not combo.get_sensitive():
            return
        self.set_progress(self._progress._fraction)
        if self.on_speed_changed:
            self.on_speed_changed(self.get_speed())

    def _on_background_press(self, gesture, _n_press, x: float, y: float) -> None:
        """Перетаскивание окна за фон: дети события не отдают, только пустые места."""
        if gesture.get_current_button() != 1:
            return
        surface = self.get_surface()
        device = gesture.get_current_event_device()
        if surface is None or device is None:
            return
        surface.begin_move(device, 1, x, y, gesture.get_current_event_time())

    def _on_enter(self, _controller, _x: float, _y: float) -> None:
        """Наведение мышью отменяет автоскрытие, как в исходной версии."""
        self._cancel_hide_timer()
        self._stop_fade()
        self._set_opacity(1.0)


def _lighten(color: str, factor: float) -> str:
    from .theme_manager import shift_color

    return shift_color(color, factor)
