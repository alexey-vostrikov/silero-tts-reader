"""Главный контроллер приложения — связывает компоненты (GTK3 / PyGObject).

Переписано с Qt на GTK: ``QApplication`` → ``Gtk.Application``, ``pyqtSignal`` →
``GLib.idle_add`` (все обновления интерфейса из рабочих потоков проходят через
``_ui()``), ``QTimer`` → ``GLib.timeout_add``, ``QSystemTrayIcon.showMessage`` →
``Gio.Notification``, ``QMessageBox`` → ``Gtk.MessageDialog``.
"""
from __future__ import annotations

import os
import queue
import signal
import threading
import traceback
from typing import Callable, Optional

# Подавить шум pynput/Xlib до любых импортов
os.environ.setdefault("PYNPUT_BACKEND", "xorg")

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import build_info, instance  # noqa: E402
from .config.config_manager import ConfigManager  # noqa: E402
from . import i18n
from .i18n import _
from .core.audio_player import AudioPlayer  # noqa: E402
from .core.hotkey_listener import HotkeyListener  # noqa: E402
from .core.text_extractor import TextExtractor  # noqa: E402
from .core.text_processor import TextProcessor  # noqa: E402,F401  (нужен для настроек)
from .core.tts_engine import TTSEngine, TTSLoadError  # noqa: E402
from .core.tts_worker import TTSWorker  # noqa: E402
from .ui.playback_widget import PlaybackWidget  # noqa: E402
from .ui.settings_window import SettingsWindow  # noqa: E402
from .ui.theme_manager import ThemeManager  # noqa: E402
from .ui.tray_icon import TrayIcon, icon_path  # noqa: E402

APP_ID = "ru.skala.silero-tts-reader"

# Сколько сегментов держим готовыми к воспроизведению. Ограничение даёт
# обратное давление: синтез не убегает вперёд и не держит в памяти всю книгу
# (у большой книги это часы звука), а идёт в темпе проигрывания.
AUDIO_QUEUE_SIZE = 6
APP_NAME = "Silero TTS Reader"
APP_VERSION = "1.0.0"



class Application(Gtk.Application):
    """Приложение целиком: конфиг, движок, плеер, трей и настройки."""

    def __init__(self, argv: list[str]) -> None:
        # HANDLES_OPEN: `silero-tts-reader текст.txt`, «Открыть с помощью» и
        # перетаскивание файла на иконку приходят в do_open, а не в activate.
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self._argv = list(argv)
        self._initialized = False

        self._config = ConfigManager()
        # Язык интерфейса поднимаем до любых строк: _() ниже опирается на него.
        i18n.init(self._config.ui_language)
        self._extractor = TextExtractor()
        self._engine = TTSEngine(
            sample_rate=self._config.sample_rate,
            models=self._config.models,
        )
        self._hotkey_listener = HotkeyListener()
        self._stop_event = threading.Event()
        self._player: Optional[AudioPlayer] = None
        self._worker: Optional[TTSWorker] = None
        self._audio_queue: queue.Queue = queue.Queue(maxsize=AUDIO_QUEUE_SIZE)

        self._theme_manager: Optional[ThemeManager] = None
        self._widget: Optional[PlaybackWidget] = None
        self._tray: Optional[TrayIcon] = None
        self._settings_dialog: Optional[SettingsWindow] = None
        self._position_timer: Optional[int] = None

    # ── Жизненный цикл ───────────────────────────────────────────────────────

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.hold()  # приложение живёт в трее, а не окном
        self._setup_actions()

    def do_activate(self) -> None:
        Gtk.Application.do_activate(self)

        # Второй запуск не должен создавать второй трей, вторую копию модели и
        # заново вешать хоткеи: GApplication передаёт его сюда же, в тот же
        # процесс. Раньше do_activate отрабатывал целиком, и каждая комбинация
        # срабатывала дважды, а libayatana ругался «Объект уже экспортирован».
        if self._initialized:
            print("  Приложение уже запущено — поднимаю виджет", flush=True)
            if self._widget is not None:
                self._widget.fade_in()
            return
        self._initialized = True

        # Себя отмечаем как работающий экземпляр: следующий запуск сравнит
        # метки сборки и, если он новее, заменит нас.
        instance.register(build_info.current())

        self._theme_manager = ThemeManager()
        self._widget = PlaybackWidget()
        self._widget.restore_position(*self._config.widget_position)
        self._wire_widget()
        # Окно привязываем к приложению: иначе действия app.* из меню не найдутся
        self.add_window(self._widget)
        self._widget.set_menu_model(self.build_menu_model())

        self._theme_manager.connect("theme-changed", lambda *_: self._update_widget_theme())
        self._theme_manager.connect("accent-changed", lambda *_: self._update_widget_theme())
        self._theme_manager.use_system_accent = self._config.use_system_accent
        self._theme_manager.set_mode(self._config.theme)
        self._update_widget_theme()

        self._tray = TrayIcon(
            on_speak_clipboard=self._on_speak_clipboard,
            on_settings=self._open_settings,
            on_about=self._show_about,
            on_quit=self._quit,
        )
        self._tray.show()
        if not self._tray.available:
            print(_(
                "  ⚠ Иконка в трее недоступна: нужен пакет gir1.2-ayatanaappindicator3-0.1\n"
                "    и расширение AppIndicator в GNOME Shell. Сообщения идут уведомлениями."
            ), flush=True)

        print(
            _("\n✓ Silero TTS Reader запущен")
            + _("\n  Горячие клавиши:")
            + _("\n    Озвучить выделенное : {hotkey}").format(
                hotkey=self._config.hotkey_speak_selection)
            + _("\n    Озвучить буфер      : {hotkey}").format(
                hotkey=self._config.hotkey_speak_clipboard)
            + _("\n    Стоп                : {hotkey}").format(
                hotkey=self._config.hotkey_stop)
            + _("\n  Иконка в трее — правая кнопка мыши для меню")
            + _("\n  Нажмите Ctrl+C в терминале для выхода\n")
            + "\n  " + _("Язык интерфейса: {language}").format(
                language=i18n.language_name())
            + "\n  " + _("Сборка: {build}").format(
                build=build_info.current().describe()),
            flush=True,
        )

        self._install_signal_handlers()
        self._register_hotkeys()
        self._load_engine()

        # Сохранять позицию виджета раз в 2 секунды
        self._position_timer = GLib.timeout_add(2000, self._save_widget_position)

    # ── Действия и горячие клавиши внутри приложения ─────────────────────────

    def _setup_actions(self) -> None:
        """Действия приложения и их сочетания клавиш.

        Глобальные хоткеи на Wayland без evdev недоступны, а эти работают,
        пока окно виджета в фокусе: без них при таком окружении приложением
        нельзя управлять вообще.
        """
        actions = {
            "settings": (self._open_settings, ["<Control>comma"]),
            "about": (self._show_about, []),
            "quit": (self._quit, ["<Control>q"]),
            "speak-clipboard": (self._on_speak_clipboard, ["<Control><Shift>c"]),
            "speak-selection": (
                lambda: self._ui(self._start_tts, "__selection__"),
                ["<Control><Shift>r"],
            ),
            "stop": (self._stop_playback, ["<Control><Shift>s"]),
        }
        for name, (callback, accels) in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    def build_menu_model(self) -> Gio.Menu:
        """Меню для виджета: те же действия, что и клавиши."""
        menu = Gio.Menu()
        menu.append(_("Озвучить буфер обмена"), "app.speak-clipboard")
        menu.append(_("Озвучить выделенное"), "app.speak-selection")
        menu.append(_("Остановить"), "app.stop")
        section = Gio.Menu()
        section.append(_("Настройки"), "app.settings")
        section.append(_("О программе"), "app.about")
        menu.append_section(None, section)
        tail = Gio.Menu()
        tail.append(_("Выход"), "app.quit")
        menu.append_section(None, tail)
        return menu

    # ── Открытие файлов ──────────────────────────────────────────────────────

    def do_open(self, files, n_files: int, _hint: str) -> None:
        """Озвучить открытые файлы: аргумент командной строки или «Открыть с помощью»."""
        if not self._initialized:
            self.do_activate()

        texts: list[str] = []
        for gfile in files:
            path = gfile.get_path()
            if not path:
                self._notify(_("Не удалось открыть файл"))
                continue
            try:
                texts.append(TextExtractor.read_text_file(path))
            except (OSError, ValueError) as exc:
                self._notify(str(exc))
        if not texts:
            return

        text = "\n\n".join(texts).strip()
        if not text:
            self._notify(_("Файл пустой"))
            return
        # Длинный текст не обрезаем и не спрашиваем: он читается целиком,
        # кусками по предложениям, а очередь синтеза ограничена — книга
        # уходит в динамик по мере готовности, а не копится в памяти.
        self._ui(self._start_tts, text)

    def _install_signal_handlers(self) -> None:
        """Штатный выход по SIGTERM: так нас просит закрыться новый экземпляр.

        Без обработчика процесс умирает мгновенно, не сохранив конфиг и не сняв
        иконку с шины, а следующий запуск получает занятое имя D-Bus.
        """
        try:
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, self._on_sigterm)
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, self._on_sigterm)
        except Exception:
            pass  # не критично: остаётся обычное поведение по умолчанию

    def _on_sigterm(self) -> bool:
        print("  Получен сигнал завершения — закрываюсь", flush=True)
        instance.clear()
        self._quit()
        return False  # одноразовый обработчик

    # ── Поток → интерфейс ────────────────────────────────────────────────────

    def _ui(self, func: Callable, *args) -> None:
        """Выполнить функцию в главном потоке GLib (замена сигналов Qt)."""
        GLib.idle_add(self._ui_call, func, args)

    @staticmethod
    def _ui_call(func: Callable, args: tuple) -> bool:
        try:
            func(*args)
        except Exception:
            traceback.print_exc()
        return False  # одноразовый вызов

    # ── Загрузка модели ──────────────────────────────────────────────────────

    def _load_engine(self) -> None:
        def _load() -> None:
            try:
                print("  Загружаю модель TTS...", flush=True)
                self._engine.load()
                speakers = self._engine.get_available_speakers()
                if self._config.speaker not in speakers and speakers:
                    self._config.speaker = speakers[0]
                    self._config.save()
                print("  ✓ " + _("Модель загружена. Голос: {voice}").format(
                      voice=self._config.speaker), flush=True)
                print("    " + _("Доступные голоса: {voices}").format(
                      voices=", ".join(speakers)), flush=True)
                # Английская модель необязательна: без неё латиница читается
                # русскими фонемами, о чём честнее сказать сразу.
                english = self._engine.get_available_speakers("en")
                if english:
                    print(
                        "  ✓ "
                        + _("Английская модель: голос {voice}, голосов {count}").format(
                            voice=self._config.speaker_for("en"), count=len(english)
                        ),
                        flush=True,
                    )
                else:
                    print("  ⚠ Английской модели нет: латиница будет читаться по-русски."
                          "\n    Скачать: silero-tts-model download en", flush=True)
            except TTSLoadError as exc:
                print("  ✗ " + _("Ошибка: {error}").format(error=exc), flush=True)
                self._ui(self._show_tts_error, str(exc))

        threading.Thread(target=_load, daemon=True, name="ModelLoader").start()

    # ── Виджет воспроизведения ───────────────────────────────────────────────

    def _wire_widget(self) -> None:
        assert self._widget is not None
        w = self._widget
        w.on_pause_resume = self._toggle_pause
        w.on_stop = self._stop_playback
        w.on_speed_changed = self._on_speed_changed
        w.on_rewind = self._on_rewind_playback
        w.on_seek_fraction = self._on_seek_fraction_playback

    def _update_widget_theme(self) -> None:
        if self._widget is None or self._theme_manager is None:
            return
        self._widget.apply_theme(
            is_dark=self._theme_manager.is_dark(),
            accent_color=self._theme_manager.accent_color(),
        )

    # ── Горячие клавиши ──────────────────────────────────────────────────────

    def _register_hotkeys(self) -> None:
        try:
            self._hotkey_listener.set_hotkeys(
                speak_selection=self._config.hotkey_speak_selection,
                speak_clipboard=self._config.hotkey_speak_clipboard,
                stop=self._config.hotkey_stop,
                on_speak_selection=lambda: self._ui(self._start_tts, "__selection__"),
                on_speak_clipboard=lambda: self._ui(self._on_speak_clipboard),
                on_stop=lambda: self._ui(self._stop_playback),
            )
        except ValueError:
            pass  # конфликт комбинаций — разбирается в настройках

    # ── Конвейер TTS ─────────────────────────────────────────────────────────

    def _start_tts(self, text_or_marker: str) -> None:
        if text_or_marker == "__selection__":
            text = self._extractor.get_selected_text()
            if not text:
                self._notify(_("Текст не выделен"))
                return
        else:
            text = text_or_marker

        self._stop_playback_sync()
        self._begin_pipeline(text)

    def _begin_pipeline(
        self,
        text: str,
        speaker: str | None = None,
        speed: float | None = None,
    ) -> None:
        if not self._engine.is_loaded:
            self._notify(_("Модель TTS ещё загружается, подождите..."))
            return

        self._stop_event = threading.Event()
        self._audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_SIZE)
        use_speed = speed if speed is not None else self._config.speed
        use_speaker = speaker if speaker is not None else self._config.speakers

        # Общее число квантов известно заранее: по нему полоска показывает место
        # в книге, а не долю текущего предложения.
        total_quanta = TTSWorker.count_quanta(text)
        self._player = AudioPlayer(
            audio_queue=self._audio_queue,
            stop_event=self._stop_event,
            sample_rate=self._config.sample_rate,
            speed=use_speed,
            total_segments=total_quanta,
            on_started=lambda: self._ui(self._on_playback_started),
            on_segment_progress=lambda v: self._ui(self._on_segment_progress, v),
            on_paused=lambda: self._ui(self._widget.set_paused, True) if self._widget else None,
            on_resumed=lambda: self._ui(self._widget.set_paused, False) if self._widget else None,
            on_finished=lambda: self._ui(self._on_playback_finished),
            on_stopped=lambda: self._ui(self._on_playback_stopped),
            on_speed_unavailable=lambda: self._ui(
                self._notify, "Изменение скорости недоступно (нет rubberband)"
            ),
        )

        self._worker = TTSWorker(
            engine=self._engine,
            text=text,
            speaker=use_speaker,
            config=self._config,
            audio_queue=self._audio_queue,
            stop_event=self._stop_event,
            on_error=lambda e: self._ui(self._show_tts_error, e),
        )

        # Окно сразу: первый квант синтезируется секунду-две, и всё это время
        # человек должен видеть, что приложение работает и что читается.
        if self._widget is not None:
            self._widget.fade_in()

        self._worker.start()
        self._player.start()

    def _on_playback_started(self) -> None:
        if self._widget is None:
            return
        # Окно уже показано в _begin_pipeline: повторный fade_in переставлял
        # его и печатал «Окно показано» дважды.
        self._widget.set_paused(False)

    def _on_segment_progress(self, value: float) -> None:
        if self._widget is not None:
            self._widget.set_progress(value)

    def _on_playback_finished(self) -> None:
        if self._widget is not None:
            self._widget.set_paused(False)
            self._widget.schedule_hide(2000)

    def _on_playback_stopped(self) -> None:
        if self._widget is not None:
            self._widget.schedule_hide(500)

    def _stop_playback(self) -> None:
        self._stop_event.set()

    def _stop_playback_sync(self) -> None:
        self._stop_event.set()
        if self._player and self._player.is_alive():
            self._player.join(timeout=1.0)

    def _toggle_pause(self) -> None:
        if self._player is None:
            return
        if self._player.is_paused:
            self._player.resume()
        else:
            self._player.pause()

    def _on_rewind_playback(self, seconds: float) -> None:
        if self._player and self._player.is_alive():
            print(f"[SileroApp] Перемотка аудио на {seconds:+.1f} сек", flush=True)
            self._player.seek_relative(seconds)

    def _on_seek_fraction_playback(self, fraction: float) -> None:
        if self._player and self._player.is_alive():
            print(f"[SileroApp] Переход к позиции {fraction * 100:.1f}%", flush=True)
            self._player.seek_fraction(fraction)

    # ── Буфер обмена ─────────────────────────────────────────────────────────

    def _on_speak_clipboard(self) -> None:
        text = self._extractor.get_clipboard_text()
        if not text:
            self._notify(_("Буфер обмена не содержит текст"))
            return
        self._start_tts(text)

    # ── Скорость ─────────────────────────────────────────────────────────────

    def _on_speed_changed(self, speed: float) -> None:
        self._config.speed = speed
        self._config.save()
        if self._player and self._player.is_alive():
            self._player.set_speed(speed)

    # ── Настройки ────────────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.present()
            return

        speakers: dict[str, list[str]] = {}
        if self._engine.is_loaded:
            speakers = {
                lang: voices
                for lang in self._engine.loaded_languages
                if (voices := self._engine.get_available_speakers(lang))
            }
        dialog = SettingsWindow(
            config=self._config,
            speakers=speakers,
            on_preview_voice=self._preview_voice,
            theme_manager=self._theme_manager,
            parent=None,  # постоянного окна у приложения нет
        )
        dialog.on_saved = self._apply_settings
        self._settings_dialog = dialog
        dialog.connect("destroy", self._on_settings_closed)
        dialog.present()

    def _on_settings_closed(self, *_args) -> None:
        self._settings_dialog = None

    def _apply_settings(self) -> None:
        if self._theme_manager is not None:
            self._theme_manager.use_system_accent = self._config.use_system_accent
            self._theme_manager.set_mode(self._config.theme)
        self._update_widget_theme()
        self._register_hotkeys()
        if self._widget is not None:
            self._widget.set_speed(self._config.speed)

    def _preview_voice(self, speaker: str, speed: float) -> None:
        self._stop_playback_sync()
        self._begin_pipeline(
            _("Привет! Это голос {speaker} со скоростью {speed}x.").format(
                speaker=speaker, speed=f"{speed:.2g}"),
            speaker=speaker,
            speed=speed,
        )

    # ── Уведомления и диалоги ────────────────────────────────────────────────

    def _notify(self, message: str, title: str = APP_NAME) -> None:
        """Всплывающее уведомление: работает и без трея (Gio.Notification)."""
        notification = Gio.Notification.new(title)
        notification.set_body(message)
        path = icon_path()
        if path:
            notification.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(path)))
        else:
            notification.set_icon(Gio.ThemedIcon.new("audio-headset"))
        try:
            self.send_notification(None, notification)
        except Exception:
            print(f"[{title}] {message}", flush=True)

    def _show_tts_error(self, error_text: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=_("Не удалось синтезировать речь"),
        )
        dialog.set_title(_("Ошибка TTS"))
        dialog.format_secondary_text(error_text)
        dialog.run()
        dialog.destroy()

    def _show_about(self) -> None:
        dialog = Gtk.AboutDialog(modal=True)
        dialog.set_program_name(APP_NAME)
        dialog.set_version(APP_VERSION)
        dialog.set_comments(_(
            "Озвучивание выделенного текста с помощью Silero TTS.\n"
            "Горячие клавиши и голос настраиваются в Настройках."
        ))
        path = icon_path()
        if path:
            try:
                dialog.set_logo(GdkPixbuf_from_file(path))
            except Exception:
                pass
        dialog.run()
        dialog.destroy()

    # ── Позиция виджета ──────────────────────────────────────────────────────

    def _save_widget_position(self) -> bool:
        if self._widget is not None and self._widget.get_visible():
            x, y = self._widget.get_position()
            self._config.widget_position = (x, y)
        return True  # повторяющийся таймер

    # ── Выход ────────────────────────────────────────────────────────────────

    def _quit(self) -> None:
        instance.clear()
        self._stop_playback_sync()
        self._hotkey_listener.stop_listening()
        self._config.save()
        if self._position_timer is not None:
            GLib.source_remove(self._position_timer)
            self._position_timer = None
        if self._tray is not None:
            self._tray.hide()
        self.release()
        self.quit()


def GdkPixbuf_from_file(path: str):
    """Логотип для диалога «О программе» (импорт здесь, чтобы не тянуть gi вверху)."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    return GdkPixbuf.Pixbuf.new_from_file(path)
