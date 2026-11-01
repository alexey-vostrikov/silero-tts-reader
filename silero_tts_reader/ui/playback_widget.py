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
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from ..core.audio_player import SEEK_STEP, SPEED_AVAILABLE  # noqa: E402
from ..i18n import _  # noqa: E402
from . import layer_shell, window_placement  # noqa: E402
from .css_scope import scope_css  # noqa: E402
from .window_icon import apply_icon  # noqa: E402

WIDGET_WIDTH = 360
WIDGET_HEIGHT = 95
# Полоса текста под прогрессом: две строки мелким шрифтом плюс отступ. Входит
# в общую высоту окна, иначе надпись обрезалась бы снизу.
TEXT_BLOCK_HEIGHT = 36
CORNER_RADIUS = 14

APP_TITLE = "Silero TTS Reader"

# Глифы кнопок. ⏸ (U+23F8) и ⏹ (U+23F9) есть только в Unifont и FreeSerif:
# система подставляет их вместо Noto, и значки выходят заметно мельче, чем ◀▶
# у перемотки. Эти символы есть в Noto и рисуются в том же масштабе.
# Папка из Noto Emoji: такой глиф узнаётся сразу и не требует подписи.
OPEN_GLYPH = "📂"
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


class PauseGlyph(Gtk.DrawingArea):
    """Знак паузы и продолжения: два бруска или треугольник.

    Раньше кнопка показывала текст ``▌▌``. Печатный знак занимает половину
    своей клетки, поэтому при центрировании по ширине строки сам рисунок
    уезжал влево, и на кнопке он выглядел сдвинутым. Здесь знак рисуется по
    фактическому размеру области, так что центрирование получается из
    геометрии, а не из метрик шрифта.
    """

    def __init__(self, state: str = "pause") -> None:
        super().__init__()
        self._state = state
        self._colour = "#ffffff"
        self.set_draw_func(self._draw)

    def set_state(self, state: str) -> None:
        """``pause`` — два бруска, ``play`` — треугольник."""
        if state == self._state:
            return
        self._state = state
        self.queue_draw()

    def set_colour(self, colour: str) -> None:
        self._colour = colour
        self.queue_draw()

    def bars(self, width: int, height: int) -> list[tuple[float, float, float, float]]:
        """Прямоугольники знака паузы: (x, y, ширина, высота).

        Геометрия вынесена из рисования, чтобы центровку можно было проверить
        без графической библиотеки: в образе сборки нет python3-cairo.
        """
        side = min(width, height) * 0.55
        cx, cy = width / 2, height / 2
        bar = side * 0.30
        gap = side * 0.22
        top = cy - side / 2
        return [
            (cx + index * (gap / 2 + bar / 2) - bar / 2, top, bar, side)
            for index in (-1, 1)
        ]

    def triangle(self, width: int, height: int) -> list[tuple[float, float]]:
        """Вершины треугольника «продолжить», оптически по центру.

        Сдвиг на 4 % влево: справа у знака пусто, а слева — сплошная кромка.
        """
        side = min(width, height) * 0.55
        cx, cy = width / 2, height / 2
        return [
            (cx - side * 0.46, cy - side / 2),
            (cx + side * 0.46, cy),
            (cx - side * 0.46, cy + side / 2),
        ]

    def _draw(self, _area, cr, width: int, height: int) -> None:
        cr.set_source_rgba(*self._rgba(self._colour))

        if self._state == "pause":
            for x, y, bar_width, bar_height in self.bars(width, height):
                self._rounded_rect(cr, x, y, bar_width, bar_height, bar_width * 0.28)
                cr.fill()
            return

        points = self.triangle(width, height)
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.close_path()
        cr.fill()

    @staticmethod
    def _rounded_rect(cr, x: float, y: float, width: float, height: float,
                      radius: float) -> None:
        cr.new_sub_path()
        cr.arc(x + width - radius, y + radius, radius, -1.5708, 0)
        cr.arc(x + width - radius, y + height - radius, radius, 0, 1.5708)
        cr.arc(x + radius, y + height - radius, radius, 1.5708, 3.1416)
        cr.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
        cr.close_path()

    @staticmethod
    def _rgba(colour: str) -> tuple[float, float, float, float]:
        value = colour.lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        red = int(value[0:2], 16) / 255
        green = int(value[2:4], 16) / 255
        blue = int(value[4:6], 16) / 255
        return red, green, blue, 1.0


class PlaybackWidget(Gtk.ApplicationWindow):
    """Полупрозрачный виджет управления воспроизведением поверх других окон."""

    def __init__(self, application=None, monitor_index: int | None = None,
                 positioning: str = "off") -> None:
        super().__init__(application=application)

        # Callbacks, которые проставляет приложение
        self.on_open_file: Callable[[], None] | None = None
        self.on_pause_resume: Callable[[], None] | None = None
        self.on_stop: Callable[[], None] | None = None
        self.on_speed_changed: Callable[[float], None] | None = None
        self.on_rewind: Callable[[float], None] | None = None
        # Кнопка «предыдущий квант»: решение принимает плеер, ему известна
        # проигранная доля фразы.
        self.on_previous: Callable[[], None] | None = None
        # Кнопка «следующий квант»: правило симметрично предыдущей.
        self.on_next: Callable[[], None] | None = None
        self.on_seek_fraction: Callable[[float], None] | None = None

        self._is_dark = True
        self._accent_color = "#6366f1"
        self._is_paused = False
        self._fade_source: int | None = None
        self._hide_timer: int | None = None
        # Глобальное позиционирование выключено по умолчанию: место окна
        # выбирает композитор, как для любого приложения. Включается в
        # настройках (вкладка «Позиционирование»), потому что требует либо
        # протокола layer-shell, либо расширения оболочки.
        self._positioning = positioning
        # Как окно поставлено: "layer" (протокол), "x11" (запрос к серверу),
        # "dbus" (расширение оболочки) или "none" (место выбрал композитор).
        self._position_mode = "none"
        self._monitor_index = monitor_index

        self._metrics = {
            "width": WIDGET_WIDTH, "height": WIDGET_HEIGHT,
            "button": 30, "corner": CORNER_RADIUS,
            "font_label": 13, "font_button": 12, "font_small": 11, "progress": 4,
            "text_block": TEXT_BLOCK_HEIGHT,
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

        # Класс на своём окне. Провайдер зарегистрирован на весь дисплей, и
        # без класса правило «window: прозрачный фон» делало прозрачными и
        # окно настроек, и любые будущие окна приложения.
        self.add_css_class("playback-window")
        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
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

        # Перетаскивание за свободное место окна — Gtk.WindowHandle, а не свой
        # GestureClick на контейнере. Свой контроллер получал нажатия, которые
        # уже ушли детям, и начинал перетаскивание окна: кнопки, список
        # скорости и пункты меню не срабатывали вовсе. WindowHandle устроен
        # ровно для этого случая — тянет окно только там, где нажатие не
        # досталось виджету.
        self._handle = Gtk.WindowHandle()
        self._handle.set_child(self._root)
        self.set_child(self._handle)

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

        self._btn_rewind = self._make_button("◀◀", _("Середина прошлой фразы, её начало или −5 секунд"),
                                             "playback-neutral")
        self._btn_rewind.connect("clicked", lambda _b: self._emit_previous())
        top_row.append(self._btn_rewind)

        self._btn_pause = Gtk.Button()
        self._btn_pause.set_size_request(30, 30)
        self._btn_pause.set_tooltip_text(_("Пауза / Продолжить"))
        self._btn_pause.add_css_class("playback-accent")
        # Знак рисуется, а не печатается: глиф «▌▌» в клетке шрифта стоит
        # не по центру, и на кнопке это видно. См. PauseGlyph.
        self._pause_glyph = PauseGlyph("pause")
        self._btn_pause.set_child(self._pause_glyph)
        self._btn_pause.connect("clicked", lambda _b: self._on_pause_resume_clicked())
        top_row.append(self._btn_pause)

        self._btn_forward = self._make_button("▶▶", _("Следующая фраза или +5 секунд"),
                                              "playback-neutral")
        self._btn_forward.connect("clicked", lambda _b: self._emit_next())
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

        # ── Текст, который уходит в синтез ───────────────────────────────────
        # Нужен, чтобы видеть фразу целиком вместе с разметкой: без него
        # неверное ударение выглядит ошибкой синтеза, хотя знак стоит в тексте.
        self._text_view = Gtk.Label()
        self._text_view.set_xalign(0.0)
        self._text_view.set_yalign(0.0)
        self._text_view.set_wrap(True)
        self._text_view.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._text_view.set_lines(2)
        self._text_view.set_ellipsize(Pango.EllipsizeMode.END)
        self._text_view.add_css_class("playback-text")
        self._text_view.set_selectable(False)
        self._text_view.set_text("")
        self._root.append(self._text_view)

        # Перетаскивание окна делает WindowHandle (см. _setup_ui), а наведение
        # мыши отменяет автоскрытие: в GTK4 это контроллер, а не сигнал окна.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_enter)
        self._root.add_controller(motion)

    def set_segment_text(self, text: str) -> None:
        """Показать фразу, ушедшую в синтез, — как есть, с разметкой.

        Пометки не украшаются и не вычищаются: смысл строки в том, чтобы
        видеть ровно то, что получила модель, включая знаки «+» и раскрытые
        сокращения.
        """
        self._text_view.set_text(text or "")

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
        # Знак паузы рисуется сам, поэтому цвет ему передаётся явно: CSS
        # применяется к кнопке, а не к содержимому области рисования.
        if hasattr(self, "_pause_glyph"):
            self._pause_glyph.set_colour("#e2e8f0" if is_dark else "#0f172a")
        font_label = self._metrics.get("font_label", 13)
        font_button = self._metrics.get("font_button", 12)
        font_small = self._metrics.get("font_small", 11)
        # Текст синтеза — вспомогательный: он не должен перетягивать внимание
        # с кнопок, поэтому приглушён относительно основного текста.
        text_colour = "rgba(226, 232, 240, 0.72)" if is_dark else "rgba(15, 23, 42, 0.70)"

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
        # Фон окна непрозрачный: прозрачность не нужна, окно должно читаться
        # на любых обоях и не требовать композитинга.
        self._css.load_from_data(
            scope_css(
                f"""
            window.playback-window {{
                background-color: {background};
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
            .playback-text {{
                color: {text_colour};
                font-size: {font_small}px;
                padding: 0;
                margin: 0;
                background: none;
            }}
            """,
                ".playback-window",
            ).encode()
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
        text_block = max(TEXT_BLOCK_HEIGHT, round(TEXT_BLOCK_HEIGHT * width / WIDGET_WIDTH))
        height = int(width * WIDGET_HEIGHT / WIDGET_WIDTH) + text_block
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
            "progress": progress, "text_block": text_block,
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
        """Поставить окно в правый нижний угол, если это включено в настройках.

        По умолчанию глобальное позиционирование выключено: место окна выбирает
        композитор, как для любого другого приложения. Включённый режим требует
        либо протокола layer-shell (не в stock-mutter, а поверхность в слое
        OVERLAY перекрывает панели и попапы), либо расширения оболочки
        «Window Calls».

        Порядок внутри включённого режима: layer-shell, затем X11, затем
        расширение оболочки по D-Bus. Первые два работают до показа окна,
        третье — только после: расширение адресует окно номером из своего
        списка, а до map окна в этом списке нет.
        """
        if self._positioning == "off":
            self._position_mode = "none"
            return

        width = self._metrics.get("width", WIDGET_WIDTH)
        height = self._metrics.get("height", WIDGET_HEIGHT)
        method = self._positioning

        if method in ("auto", "layer"):
            self._position_mode = layer_shell.place_bottom_right(
                self, width, height, margin=16, monitor_index=self._monitor_index)
            if self._position_mode != "none" or method == "layer":
                return

        # Расширение оболочки: точный способ под Wayland, но окно в его списке
        # появляется только после показа, поэтому позиция ставится отложенно.
        if method in ("auto", "shell") and window_placement.available():
            self._position_mode = "dbus-pending"

    def set_positioning(self, positioning: str) -> None:
        """Сменить режим позиционирования (вкладка настроек «Позиционирование»).

        Смена вступает в силу сразу: следующая установка позиции пойдёт по
        новому режиму, без перезапуска приложения.
        """
        if positioning == self._positioning:
            return
        self._positioning = positioning
        self._place_window()

    def _place_after_show(self) -> None:
        """Доделать позицию через хелпер, когда окно уже показано."""
        if self._position_mode != "dbus-pending":
            return
        width = self._metrics.get("width", WIDGET_WIDTH)
        height = self._metrics.get("height", WIDGET_HEIGHT)
        mode = window_placement.place_bottom_right(
            self, width, height, margin=16, monitor_index=self._monitor_index)
        self._position_mode = mode if mode != "none" else "dbus-failed"

    def _place_after_show_source(self) -> bool:
        """Обёртка таймера: одноразовый вызов доработки позиции."""
        self._place_after_show()
        return False

    def restore_position(self, x: int, y: int) -> None:
        """Вернуть окно на сохранённое место (только если это возможно).

        Позицию задаёт композитор, если глобальное позиционирование выключено
        (по умолчанию) или недоступно — тогда вызов честно ничего не делает.
        """
        if self._positioning == "off":
            return
        if self._position_mode in ("layer", "dbus-failed"):
            return
        if x < 0 or y < 0:
            self._place_window()
            return
        if self._position_mode == "x11":
            layer_shell.move_x11(self, x, y)
            return
        if self._position_mode in ("dbus", "dbus-pending"):
            # Сохранённая позиция в логических пикселях, а Move ждёт физические.
            monitor = self._monitor()
            scale = max(1, monitor.get_scale_factor()) if monitor is not None else 1
            window_id = window_placement.find_window(
                title=self.get_title(),
                size=(self._metrics.get("width", WIDGET_WIDTH) * scale,
                      self._metrics.get("height", WIDGET_HEIGHT) * scale))
            if window_id is not None:
                window_placement.move_window(window_id, x * scale, y * scale)

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
        # Хелперу оболочки нужен номер окна, а он есть только после map.
        # Небольшая пауза: окно должно успеть попасть в список композитора.
        if self._position_mode == "dbus-pending":
            GLib.timeout_add(120, self._place_after_show_source)
        # Один раз в журнал: видно ли окно на самом деле. Без этой строки
        # «окно не появилось» приходится угадывать.
        #
        # Размер читаем не сразу: в этот момент виджет ещё не разложен, и
        # get_width() честно возвращает 0, хотя окно на экране нормальное.
        # Строка «0×0» в журнале сбивала с толку при разборе жалоб на окно.
        # i18n: нет перевода — строка журнала в консоли
        print(
            f"  Окно показано: запрошено {self.get_width() or self.get_default_size()[0]}"
            f"×{self.get_height() or self.get_default_size()[1]}"
            f", позиция {self._position_mode}, видимо {self.get_visible()}",
            flush=True,
        )
        GLib.timeout_add(300, self._log_real_size)
        return False   # одноразовый idle

    def _log_real_size(self) -> bool:
        """Настоящий размер после раскладки — одной строкой."""
        # i18n: нет перевода — строка журнала в консоли
        print(
            f"  Окно разложено: {self.get_width()}×{self.get_height()}",
            flush=True,
        )
        return False

    def schedule_hide(self, delay_ms: int = 2000) -> None:
        self._cancel_hide_timer()
        self._hide_timer = GLib.timeout_add(max(1, int(delay_ms)), self._begin_fade_out)

    def _begin_fade_out(self) -> bool:
        """Скрыть окно после паузы бездействия.

        Затухание убрано вместе с прозрачностью: окно исчезает разом.
        """
        self._hide_timer = None
        self.set_visible(False)
        return False  # одноразовый таймер

    def _set_opacity(self, value: float) -> None:
        """Совместимость: прозрачности больше нет, значение запоминается.

        Метод оставлен, потому что его вызывают места, где раньше
        восстанавливали видимость окна после затухания.
        """
        self._opacity = max(0.0, min(1.0, float(value)))

    def get_opacity(self) -> float:
        """Текущее значение — всегда 1.0; для проверок и журнала."""
        return getattr(self, "_opacity", 1.0)

    def _stop_fade(self) -> None:
        """Совместимость: затухания больше нет, останавливать нечего."""
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
        self._pause_glyph.set_state("play" if paused else "pause")
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

    def _emit_next(self) -> None:
        """Перейти к следующей фразе или перемотать вперёд."""
        if self.on_next:
            self.on_next()
        else:
            self._emit_rewind(SEEK_STEP)

    def _emit_previous(self) -> None:
        """Вернуться к предыдущей фразе или начать текущую заново."""
        if self.on_previous:
            self.on_previous()
        else:
            # Запасной путь: без обработчика ведём себя как раньше.
            self._emit_rewind(-SEEK_STEP)

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

    def _on_enter(self, _controller, _x: float, _y: float) -> None:
        """Наведение мышью отменяет автоскрытие, как в исходной версии."""
        self._cancel_hide_timer()
        self._stop_fade()
        self._set_opacity(1.0)


def _lighten(color: str, factor: float) -> str:
    from .theme_manager import shift_color

    return shift_color(color, factor)
