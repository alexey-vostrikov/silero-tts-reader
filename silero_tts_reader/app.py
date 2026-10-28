"""Главный контроллер приложения — связывает компоненты (GTK3 / PyGObject).

Переписано с Qt на GTK: ``QApplication`` → ``Gtk.Application``, ``pyqtSignal`` →
``GLib.idle_add`` (все обновления интерфейса из рабочих потоков проходят через
``_ui()``), ``QTimer`` → ``GLib.timeout_add``, ``QSystemTrayIcon.showMessage`` →
``Gio.Notification``, ``QMessageBox`` → ``Gtk.MessageDialog``.
"""
from __future__ import annotations

import os
import queue
import threading
import traceback
from typing import Callable, Optional

# Подавить шум pynput/Xlib до любых импортов
os.environ.setdefault("PYNPUT_BACKEND", "xorg")

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .config.config_manager import ConfigManager  # noqa: E402
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
APP_NAME = "Silero TTS Reader"
APP_VERSION = "1.0.0"

RESPONSE_SPEAK_ALL = 10
RESPONSE_TRIM = 11


class Application(Gtk.Application):
    """Приложение целиком: конфиг, движок, плеер, трей и настройки."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__(application_id=APP_ID)
        self._argv = list(argv)

        self._config = ConfigManager()
        self._extractor = TextExtractor()
        self._engine = TTSEngine(
            model_path=self._config.model_path,
            sample_rate=self._config.sample_rate,
        )
        self._hotkey_listener = HotkeyListener()
        self._stop_event = threading.Event()
        self._player: Optional[AudioPlayer] = None
        self._worker: Optional[TTSWorker] = None
        self._audio_queue: queue.Queue = queue.Queue()

        self._theme_manager: Optional[ThemeManager] = None
        self._widget: Optional[PlaybackWidget] = None
        self._tray: Optional[TrayIcon] = None
        self._settings_dialog: Optional[SettingsWindow] = None
        self._position_timer: Optional[int] = None

    # ── Жизненный цикл ───────────────────────────────────────────────────────

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.hold()  # приложение живёт в трее, а не окном

    def do_activate(self) -> None:
        Gtk.Application.do_activate(self)

        self._theme_manager = ThemeManager()
        self._widget = PlaybackWidget()
        self._widget.restore_position(*self._config.widget_position)
        self._wire_widget()

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
            print(
                "  ⚠ Иконка в трее недоступна: нужен пакет gir1.2-ayatanaappindicator3-0.1\n"
                "    и расширение AppIndicator в GNOME Shell. Сообщения идут уведомлениями.",
                flush=True,
            )

        print(
            "\n✓ Silero TTS Reader запущен"
            "\n  Горячие клавиши:"
            f"\n    Озвучить выделенное : {self._config.hotkey_speak_selection}"
            f"\n    Озвучить буфер      : {self._config.hotkey_speak_clipboard}"
            f"\n    Стоп                : {self._config.hotkey_stop}"
            "\n  Иконка в трее — правая кнопка мыши для меню"
            "\n  Нажмите Ctrl+C в терминале для выхода\n",
            flush=True,
        )

        self._register_hotkeys()
        self._load_engine()

        # Сохранять позицию виджета раз в 2 секунды
        self._position_timer = GLib.timeout_add(2000, self._save_widget_position)

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
                print(f"  ✓ Модель загружена. Голос: {self._config.speaker}", flush=True)
                print(f"    Доступные голоса: {', '.join(speakers)}", flush=True)
            except TTSLoadError as exc:
                print(f"  ✗ Ошибка: {exc}", flush=True)
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
                self._notify("Текст не выделен")
                return
        else:
            text = text_or_marker

        if len(text) > TextExtractor.MAX_CHARS:
            self._ask_long_text(text)
            return

        self._stop_playback_sync()
        self._begin_pipeline(text)

    def _begin_pipeline(
        self,
        text: str,
        speaker: str | None = None,
        speed: float | None = None,
    ) -> None:
        if not self._engine.is_loaded:
            self._notify("Модель TTS ещё загружается, подождите...")
            return

        self._stop_event = threading.Event()
        self._audio_queue = queue.Queue()
        use_speed = speed if speed is not None else self._config.speed
        use_speaker = speaker if speaker is not None else self._config.speaker

        self._player = AudioPlayer(
            audio_queue=self._audio_queue,
            stop_event=self._stop_event,
            sample_rate=self._config.sample_rate,
            speed=use_speed,
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

        self._worker.start()
        self._player.start()

    def _on_playback_started(self) -> None:
        if self._widget is None:
            return
        self._widget.fade_in()
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
            self._notify("Буфер обмена не содержит текст")
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

        speakers = self._engine.get_available_speakers() if self._engine.is_loaded else []
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
            f"Привет! Это голос {speaker} со скоростью {speed:.2g}x.",
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
            text="Не удалось синтезировать речь",
        )
        dialog.set_title("Ошибка TTS")
        dialog.format_secondary_text(error_text)
        dialog.run()
        dialog.destroy()

    def _ask_long_text(self, text: str) -> None:
        dialog = Gtk.Dialog(title="Длинный текст", modal=True)
        dialog.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dialog.add_button("Обрезать до лимита", RESPONSE_TRIM)
        dialog.add_button("Озвучить полностью", RESPONSE_SPEAK_ALL)
        dialog.set_default_response(RESPONSE_TRIM)

        box = dialog.get_content_area()
        box.set_border_width(16)
        box.set_spacing(8)
        label = Gtk.Label(
            label=(
                f"Текст содержит {len(text):,} символов "
                f"(лимит: {TextExtractor.MAX_CHARS:,}).\nЧто сделать?"
            ).replace(",", " ")
        )
        label.set_xalign(0.0)
        box.pack_start(label, False, False, 0)
        dialog.show_all()

        response = dialog.run()
        dialog.destroy()

        if response == RESPONSE_SPEAK_ALL:
            self._begin_pipeline(text)
        elif response == RESPONSE_TRIM:
            self._begin_pipeline(text[: TextExtractor.MAX_CHARS])

    def _show_about(self) -> None:
        dialog = Gtk.AboutDialog(modal=True)
        dialog.set_program_name(APP_NAME)
        dialog.set_version(APP_VERSION)
        dialog.set_comments(
            "Озвучивание выделенного текста с помощью Silero TTS.\n"
            "Горячие клавиши и голос настраиваются в Настройках."
        )
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
