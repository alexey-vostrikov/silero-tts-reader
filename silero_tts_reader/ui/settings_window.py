"""Settings dialog with hotkey capture and voice/audio configuration."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent
from PyQt6.QtGui import QFont, QKeySequence, QColor
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSlider, QLineEdit, QTextEdit,
    QFormLayout, QDialogButtonBox, QMessageBox, QFrame,
    QGroupBox, QCheckBox,
)

from ..core.hotkey_listener import HotkeyListener
from ..core.audio_player import SPEED_AVAILABLE
from ..core.text_processor import TextProcessor
from ..config.config_manager import ConfigManager
from .theme_manager import ThemeManager


class HotkeyCapture(QPushButton):
    """A custom button widget that captures key combinations for hotkey settings."""

    hotkey_captured = pyqtSignal(str)   # emits pynput-format string

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._capturing = False
        self._current_hotkey = ""
        self._accent_color = "#6366f1"
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

    def set_accent(self, accent_color: str = "#6366f1") -> None:
        self._accent_color = accent_color
        self._update_style()

    def _update_style(self) -> None:
        accent = getattr(self, "_accent_color", "#6366f1")
        if self._capturing:
            qacc = QColor(accent)
            bg_capturing = f"rgba({qacc.red()}, {qacc.green()}, {qacc.blue()}, 0.2)"
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {bg_capturing};
                    color: #ffffff;
                    border: 2px solid {accent};
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 13px;
                    font-weight: bold;
                    text-align: left;
                    outline: none;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.06);
                    color: #ffffff;
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 13px;
                    text-align: left;
                    outline: none;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.1);
                    border-color: {accent};
                    color: #ffffff;
                }}
                QPushButton:focus {{
                    border-color: {accent};
                    color: #ffffff;
                }}
            """)

    def set_hotkey(self, hotkey_str: str) -> None:
        self._current_hotkey = hotkey_str
        self._capturing = False
        display = HotkeyListener.format_hotkey(hotkey_str) if hotkey_str else "Кликните для выбора..."
        self.setText(display)
        self._update_style()

    def get_hotkey(self) -> str:
        return self._current_hotkey

    def mousePressEvent(self, event) -> None:
        if not self._capturing:
            self._start_capture()
        super().mousePressEvent(event)

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._capturing = False
            display = HotkeyListener.format_hotkey(self._current_hotkey) if self._current_hotkey else "Кликните для выбора..."
            self.setText(display)
            self._update_style()
        super().focusOutEvent(event)

    def _start_capture(self) -> None:
        self._capturing = True
        self._draft_hotkey = ""
        self.setText("Нажмите новую клавишу...")
        self._update_style()
        self.setFocus()

    def event(self, event: QEvent) -> bool:
        if self._capturing and event.type() in (
            QEvent.Type.ShortcutOverride,
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        ):
            if event.type() == QEvent.Type.KeyRelease:
                self.keyReleaseEvent(event)
            else:
                self.keyPressEvent(event)
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        modifiers = event.modifiers()

        mod_key_map = {
            Qt.Key.Key_Control: "<ctrl>",
            Qt.Key.Key_Alt: "<alt>",
            Qt.Key.Key_Shift: "<shift>",
            Qt.Key.Key_Meta: "<super>",
            Qt.Key.Key_Super_L: "<super>",
            Qt.Key.Key_Super_R: "<super>",
        }

        # If a modifier key is pressed
        if key in mod_key_map:
            parts = []
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                parts.append("<ctrl>")
            if modifiers & Qt.KeyboardModifier.AltModifier:
                parts.append("<alt>")
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                parts.append("<shift>")
            if modifiers & Qt.KeyboardModifier.MetaModifier:
                parts.append("<super>")

            main_mod = mod_key_map[key]
            if main_mod not in parts:
                parts.append(main_mod)

            draft = "+".join(parts)
            self._draft_hotkey = draft
            display = HotkeyListener.format_hotkey(draft) + "..."
            self.setText(display)
            return

        # Regular (non-modifier) key press
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("<ctrl>")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("<alt>")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("<shift>")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("<super>")

        key_str = self._qt_key_to_name(key)
        if key_str:
            parts.append(key_str)

        if parts:
            hotkey = "+".join(parts)
            self._draft_hotkey = ""
            self.set_hotkey(hotkey)
            self.hotkey_captured.emit(hotkey)

    def keyReleaseEvent(self, event) -> None:
        if not self._capturing:
            super().keyReleaseEvent(event)
            return

        key = event.key()
        mod_key_map = {
            Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
            Qt.Key.Key_Meta, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R
        }
        if key in mod_key_map and getattr(self, "_draft_hotkey", ""):
            modifiers = event.modifiers()
            active_mods = (
                Qt.KeyboardModifier.ControlModifier |
                Qt.KeyboardModifier.AltModifier |
                Qt.KeyboardModifier.ShiftModifier |
                Qt.KeyboardModifier.MetaModifier
            )
            if not (modifiers & active_mods):
                hotkey = self._draft_hotkey
                self._draft_hotkey = ""
                self.set_hotkey(hotkey)
                self.hotkey_captured.emit(hotkey)

    @staticmethod
    def _qt_key_to_name(key: int) -> str | None:
        modifiers_map = {
            Qt.Key.Key_Control: "ctrl",
            Qt.Key.Key_Alt: "alt",
            Qt.Key.Key_Shift: "shift",
            Qt.Key.Key_Meta: "super",
            Qt.Key.Key_Super_L: "super",
            Qt.Key.Key_Super_R: "super",
        }
        if key in modifiers_map:
            return modifiers_map[key]

        if 65 <= key <= 90:  # A-Z
            return chr(key).lower()
        if 48 <= key <= 57:  # 0-9
            return chr(key)
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            return f"f{key - Qt.Key.Key_F1 + 1}"

        special = {
            Qt.Key.Key_Space: "space",
            Qt.Key.Key_Tab: "tab",
            Qt.Key.Key_Return: "enter",
            Qt.Key.Key_Enter: "enter",
            Qt.Key.Key_Escape: "escape",
            Qt.Key.Key_Backspace: "backspace",
            Qt.Key.Key_Delete: "delete",
            Qt.Key.Key_Insert: "insert",
            Qt.Key.Key_Home: "home",
            Qt.Key.Key_End: "end",
            Qt.Key.Key_PageUp: "page_up",
            Qt.Key.Key_PageDown: "page_down",
            Qt.Key.Key_CapsLock: "caps_lock",
            Qt.Key.Key_NumLock: "num_lock",
            Qt.Key.Key_Pause: "pause",
            Qt.Key.Key_Print: "print_screen",
        }
        if key in special:
            return special[key]

        seq = QKeySequence(key).toString().lower()
        return seq if seq else None


class SettingsWindow(QDialog):
    """Settings dialog: hotkeys + voice/audio configuration."""

    settings_saved = pyqtSignal()

    def __init__(
        self,
        config: ConfigManager,
        speakers: list[str],
        on_preview_voice: Callable[[str, float], None],
        theme_manager: ThemeManager | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._speakers = speakers
        self._on_preview_voice = on_preview_voice
        self._theme_manager = theme_manager

        # Pending changes (not yet saved)
        self._pending: dict[str, object] = {}

        self.setWindowTitle("Настройки — Silero TTS Reader")
        self.setFixedSize(620, 500)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        if self._theme_manager:
            self._theme_manager.theme_changed.connect(self._apply_theme)
            self._theme_manager.accent_changed.connect(lambda: self._apply_theme(self._theme_manager.is_dark()))
            is_dark = self._theme_manager.is_dark()
        else:
            is_dark = True

        self._build_ui()
        self._apply_theme(is_dark)

    def _apply_theme(self, is_dark: bool = True) -> None:
        accent = self._theme_manager.accent_color() if self._theme_manager else "#6366f1"
        hover = self._theme_manager.accent_hover_color() if self._theme_manager else "#4f46e5"

        if hasattr(self, "_hk_selection"):
            self._hk_selection.set_accent(accent)
        if hasattr(self, "_hk_clipboard"):
            self._hk_clipboard.set_accent(accent)
        if hasattr(self, "_hk_stop"):
            self._hk_stop.set_accent(accent)

        if is_dark:
            self.setStyleSheet(f"""
                QDialog {{
                    background: #18181b;
                    color: #f4f4f5;
                }}
                QTabWidget::pane {{
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 8px;
                    background: #27272a;
                }}
                QTabBar::tab {{
                    background: transparent;
                    color: #a1a1aa;
                    padding: 8px 24px;
                    border-bottom: 2px solid transparent;
                    font-size: 16px;
                }}
                QTabBar::tab:selected {{
                    color: {accent};
                    border-bottom: 2px solid {accent};
                }}
                QTabBar QToolButton {{
                    background: #27272a;
                    border: 1px solid rgba(255,255,255,0.1);
                    border-radius: 4px;
                    color: #a1a1aa;
                }}
                QTabBar QToolButton:hover {{
                    background: {accent};
                    color: white;
                }}
                QLabel {{ color: #f4f4f5; font-size: 13px; }}
                QGroupBox {{
                    color: {accent};
                    font-size: 11px;
                    font-weight: 600;
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 8px;
                    margin-top: 8px;
                    padding: 8px;
                }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {accent}; font-weight: bold; }}
                QComboBox {{
                    background: rgba(255,255,255,0.06);
                    color: #f4f4f5;
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 6px;
                    padding: 5px 10px;
                    min-width: 160px;
                }}
                QComboBox QAbstractItemView {{
                    background: #27272a;
                    color: #f4f4f5;
                    selection-background-color: {accent};
                }}
                QCheckBox {{ color: #f4f4f5; font-size: 13px; spacing: 8px; }}
                QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.06); }}
                QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
                QPushButton {{
                    background: {accent};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 7px 16px;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: {hover}; }}
                QPushButton#btnReset {{
                    background: rgba(239,68,68,0.2);
                    color: #ef4444;
                    border: 1px solid rgba(239,68,68,0.4);
                }}
                QPushButton#btnReset:hover {{ background: rgba(239,68,68,0.4); }}
                QPushButton#btnPreview {{
                    background: {accent};
                    color: white;
                    padding: 5px 12px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QDialog {{
                    background: #f8fafc;
                    color: #0f172a;
                }}
                QTabWidget::pane {{
                    border: 1px solid rgba(0,0,0,0.1);
                    border-radius: 8px;
                    background: #ffffff;
                }}
                QTabBar::tab {{
                    background: transparent;
                    color: #64748b;
                    padding: 8px 24px;
                    border-bottom: 2px solid transparent;
                    font-size: 16px;
                }}
                QTabBar::tab:selected {{
                    color: {accent};
                    border-bottom: 2px solid {accent};
                }}
                QTabBar QToolButton {{
                    background: #f1f5f9;
                    border: 1px solid #cbd5e1;
                    border-radius: 4px;
                    color: #64748b;
                }}
                QTabBar QToolButton:hover {{
                    background: {accent};
                    color: white;
                }}
                QLabel {{ color: #0f172a; font-size: 13px; }}
                QGroupBox {{
                    color: {accent};
                    font-size: 11px;
                    font-weight: 600;
                    border: 1px solid rgba(0,0,0,0.12);
                    border-radius: 8px;
                    margin-top: 8px;
                    padding: 8px;
                }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {accent}; font-weight: bold; }}
                QComboBox {{
                    background: #f1f5f9;
                    color: #0f172a;
                    border: 1px solid #cbd5e1;
                    border-radius: 6px;
                    padding: 5px 10px;
                    min-width: 160px;
                }}
                QComboBox QAbstractItemView {{
                    background: #ffffff;
                    color: #0f172a;
                    selection-background-color: {accent};
                }}
                QCheckBox {{ color: #0f172a; font-size: 13px; spacing: 8px; }}
                QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid #cbd5e1; background: #f1f5f9; }}
                QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
                QPushButton {{
                    background: {accent};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 7px 16px;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: {hover}; }}
                QPushButton#btnReset {{
                    background: #fee2e2;
                    color: #dc2626;
                    border: 1px solid #fca5a5;
                }}
                QPushButton#btnReset:hover {{ background: #fecaca; }}
                QPushButton#btnPreview {{
                    background: {accent};
                    color: white;
                    padding: 5px 12px;
                }}
            """)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._build_hotkeys_tab(), "⌨️")
        tabs.setTabToolTip(0, "Горячие клавиши")
        tabs.addTab(self._build_voice_tab(), "🎭")
        tabs.setTabToolTip(1, "Голос и аудио")
        tabs.addTab(self._build_appearance_tab(), "🎨")
        tabs.setTabToolTip(2, "Внешний вид")
        tabs.addTab(self._build_ai_tab(), "🤖")
        tabs.setTabToolTip(3, "ИИ & Транслитерация")
        layout.addWidget(tabs)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        btn_reset = QPushButton("Сброс к умолчаниям")
        btn_reset.setObjectName("btnReset")
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)

        btn_row.addStretch()

        btn_cancel = QPushButton("Отмена")
        btn_cancel.setStyleSheet("background: rgba(255,255,255,0.08); color: #94a3b8;")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("  Сохранить")
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    # ── Hotkeys tab ───────────────────────────────────────────────────────────

    def _build_hotkeys_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(12)

        group = QGroupBox("ГЛОБАЛЬНЫЕ ГОРЯЧИЕ КЛАВИШИ")
        form = QFormLayout(group)
        form.setSpacing(10)

        accent = self._theme_manager.accent_color() if self._theme_manager else "#6366f1"

        self._hk_selection = HotkeyCapture()
        self._hk_selection.set_accent(accent)
        self._hk_selection.set_hotkey(self._config.hotkey_speak_selection)
        self._hk_selection.hotkey_captured.connect(
            lambda v: self._pending.update({"hotkey_speak_selection": v})
        )
        form.addRow("Озвучить выделенное:", self._hk_selection)

        self._hk_clipboard = HotkeyCapture()
        self._hk_clipboard.set_accent(accent)
        self._hk_clipboard.set_hotkey(self._config.hotkey_speak_clipboard)
        self._hk_clipboard.hotkey_captured.connect(
            lambda v: self._pending.update({"hotkey_speak_clipboard": v})
        )
        form.addRow("Озвучить буфер обмена:", self._hk_clipboard)

        self._hk_stop = HotkeyCapture()
        self._hk_stop.set_accent(accent)
        self._hk_stop.set_hotkey(self._config.hotkey_stop)
        self._hk_stop.hotkey_captured.connect(
            lambda v: self._pending.update({"hotkey_stop": v})
        )
        form.addRow("Остановить:", self._hk_stop)

        layout.addWidget(group)

        hint = QLabel(
            "💡 Кликните по полю и нажмите желаемую комбинацию клавиш.\n"
            "Работает только на X11 / XWayland."
        )
        hint.setStyleSheet("color: #64748b; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

        return tab

    # ── Voice/audio tab ───────────────────────────────────────────────────────

    def _build_voice_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(12)

        # Voice selector
        voice_group = QGroupBox("ГОЛОС")
        voice_layout = QHBoxLayout(voice_group)

        self._voice_combo = QComboBox()
        for speaker in (self._speakers or ["xenia"]):
            self._voice_combo.addItem(speaker)

        current = self._config.speaker
        idx = self._voice_combo.findText(current)
        if idx >= 0:
            self._voice_combo.setCurrentIndex(idx)
        self._voice_combo.currentTextChanged.connect(
            lambda v: self._pending.update({"speaker": v})
        )
        voice_layout.addWidget(self._voice_combo)

        btn_preview = QPushButton("▶  Прослушать")
        btn_preview.setObjectName("btnPreview")
        btn_preview.clicked.connect(self._on_preview)
        voice_layout.addWidget(btn_preview)
        layout.addWidget(voice_group)

        # Speed
        speed_group = QGroupBox("СКОРОСТЬ ВОСПРОИЗВЕДЕНИЯ ПО УМОЛЧАНИЮ")
        speed_layout = QHBoxLayout(speed_group)

        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(2, 12)
        self._speed_slider.setValue(int(round(self._config.speed / 0.25)))
        self._speed_slider.setEnabled(SPEED_AVAILABLE)
        self._speed_slider.valueChanged.connect(self._on_speed_slider)
        speed_layout.addWidget(self._speed_slider)

        self._speed_label = QLabel(f"{self._config.speed:.2g}×")
        self._speed_label.setMinimumWidth(36)
        speed_layout.addWidget(self._speed_label)

        if not SPEED_AVAILABLE:
            no_rb = QLabel("  (требуется rubberband-cli + pyrubberband)")
            no_rb.setStyleSheet("color: #f59e0b; font-size: 11px;")
            speed_layout.addWidget(no_rb)

        layout.addWidget(speed_group)
        layout.addStretch()

        return tab

    # ── Appearance tab ────────────────────────────────────────────────────────

    def _build_appearance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(12)

        grp = QGroupBox("ОФОРМЛЕНИЕ И ТЕМА ИНТЕРФЕЙСА")
        form = QFormLayout(grp)
        form.setSpacing(10)

        self._theme_combo = QComboBox()
        self._theme_combo.addItem("Авто (следовать системной теме)", "system")
        self._theme_combo.addItem("Тёмная тема", "dark")
        self._theme_combo.addItem("Светлая тема", "light")

        current_theme = self._pending.get("theme", self._config.theme)
        idx = self._theme_combo.findData(current_theme)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)

        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("Тема приложения:", self._theme_combo)

        self._accent_chk = QCheckBox("Использовать системный акцентный цвет")
        use_sys_acc = self._pending.get("use_system_accent", self._config.use_system_accent)
        self._accent_chk.setChecked(bool(use_sys_acc))
        self._accent_chk.toggled.connect(self._on_accent_toggled)
        form.addRow("", self._accent_chk)

        layout.addWidget(grp)
        layout.addStretch()

        return tab

    # ── AI & Transliteration tab ──────────────────────────────────────────────

    def _build_ai_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(10)

        # Basic Transliteration Group
        trans_group = QGroupBox("ТРАНСЛИТЕРАЦИЯ")
        trans_form = QFormLayout(trans_group)
        trans_form.setSpacing(8)

        self._chk_ignore_urls = QCheckBox("Игнорировать ссылки (http/https/www)")
        self._chk_ignore_urls.setChecked(self._config.ignore_urls)
        trans_form.addRow(self._chk_ignore_urls)

        self._chk_transliterate = QCheckBox("Переписывать латинские/иностранные слова кириллицей")
        self._chk_transliterate.setChecked(self._config.transliteration_enabled)
        trans_form.addRow(self._chk_transliterate)

        self._chk_use_llm = QCheckBox("Использовать ИИ (LLM) для фонетической транскрипции")
        self._chk_use_llm.setChecked(self._config.transliteration_use_llm)
        self._chk_use_llm.toggled.connect(lambda c: self._chk_transliterate.setChecked(True) if c else None)
        trans_form.addRow(self._chk_use_llm)

        layout.addWidget(trans_group)

        # LLM API Settings Group
        llm_group = QGroupBox("НАСТРОЙКИ ИИ-МОДЕЛИ (API)")
        llm_form = QFormLayout(llm_group)
        llm_form.setSpacing(8)

        self._llm_provider_combo = QComboBox()
        self._llm_provider_combo.addItem("Ollama (Локальный)", "ollama")
        self._llm_provider_combo.addItem("OpenAI (Облачный)", "openai")
        self._llm_provider_combo.addItem("Пользовательский (Custom API)", "custom")

        curr_prov = self._config.transliteration_provider
        idx = self._llm_provider_combo.findData(curr_prov)
        if idx >= 0:
            self._llm_provider_combo.setCurrentIndex(idx)
        self._llm_provider_combo.currentIndexChanged.connect(self._on_llm_provider_changed)

        llm_form.addRow("Провайдер:", self._llm_provider_combo)

        self._txt_base_url = QLineEdit(self._config.transliteration_base_url)
        self._txt_base_url.setPlaceholderText("http://localhost:11434/v1")
        llm_form.addRow("API Base URL:", self._txt_base_url)

        self._txt_api_key = QLineEdit(self._config.transliteration_api_key)
        self._txt_api_key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._txt_api_key.setPlaceholderText("ollama / sk-...")
        llm_form.addRow("API Key:", self._txt_api_key)

        self._txt_model = QLineEdit(self._config.transliteration_model)
        self._txt_model.setPlaceholderText("qwen2.5 / llama3 / gpt-4o-mini")
        llm_form.addRow("Название модели:", self._txt_model)

        self._txt_system_prompt = QTextEdit()
        self._txt_system_prompt.setPlainText(self._config.transliteration_system_prompt)
        self._txt_system_prompt.setMaximumHeight(65)
        self._txt_system_prompt.setToolTip("Системный промпт ИИ с правилом расстановки знака ударения '+'")
        llm_form.addRow("Системный промпт ИИ:", self._txt_system_prompt)

        btn_test = QPushButton("🧪 Проверить подключение ИИ")
        btn_test.setObjectName("btnPreview")
        btn_test.clicked.connect(self._on_test_llm_connection)
        llm_form.addRow("", btn_test)

        layout.addWidget(llm_group)

        hint = QLabel(
            "💡 При недоступности ИИ или отключении опции применяется быстрый встроенный офлайн-фонетизатор."
        )
        hint.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

        return tab

    def _on_llm_provider_changed(self, index: int) -> None:
        prov = self._llm_provider_combo.itemData(index)
        if prov == "ollama":
            self._txt_base_url.setText("http://localhost:11434/v1")
            self._txt_api_key.setText("ollama")
            if not self._txt_model.text():
                self._txt_model.setText("qwen2.5")
        elif prov == "openai":
            self._txt_base_url.setText("https://api.openai.com/v1")
            if not self._txt_model.text():
                self._txt_model.setText("gpt-4o-mini")

    def _on_test_llm_connection(self) -> None:
        url = self._txt_base_url.text().strip()
        key = self._txt_api_key.text().strip()
        model = self._txt_model.text().strip()
        prompt = self._txt_system_prompt.toPlainText().strip() if hasattr(self, "_txt_system_prompt") else None

        ok, msg = TextProcessor.test_llm_connection(
            base_url=url, api_key=key, model=model, timeout=4.0, system_prompt=prompt
        )
        if ok:
            QMessageBox.information(self, "Тест ИИ API", msg)
        else:
            QMessageBox.warning(self, "Ошибка ИИ API", msg)

    def _on_theme_changed(self, index: int) -> None:
        theme = self._theme_combo.itemData(index)
        self._pending["theme"] = theme
        if self._theme_manager:
            self._theme_manager.set_mode(theme)

    def _on_accent_toggled(self, checked: bool) -> None:
        self._pending["use_system_accent"] = checked
        if self._theme_manager:
            self._theme_manager.use_system_accent = checked

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_preview(self) -> None:
        speaker = self._voice_combo.currentText()
        speed = self._speed_slider.value() * 0.25
        self._on_preview_voice(speaker, speed)

    def _on_speed_slider(self, tick: int) -> None:
        speed = tick * 0.25
        self._speed_label.setText(f"{speed:.2g}×")
        self._pending["speed"] = speed

    def _on_save(self) -> None:
        # Guarantee direct read of current hotkeys from controls
        if hasattr(self, "_hk_selection"):
            self._config.hotkey_speak_selection = self._hk_selection.get_hotkey()
        if hasattr(self, "_hk_clipboard"):
            self._config.hotkey_speak_clipboard = self._hk_clipboard.get_hotkey()
        if hasattr(self, "_hk_stop"):
            self._config.hotkey_stop = self._hk_stop.get_hotkey()

        # Save AI / Transliteration settings
        if hasattr(self, "_chk_ignore_urls"):
            self._config.ignore_urls = self._chk_ignore_urls.isChecked()
        if hasattr(self, "_chk_transliterate") and hasattr(self, "_chk_use_llm"):
            use_llm = self._chk_use_llm.isChecked()
            trans_enabled = self._chk_transliterate.isChecked() or use_llm
            self._config.transliteration_use_llm = use_llm
            self._config.transliteration_enabled = trans_enabled
        if hasattr(self, "_llm_provider_combo"):
            self._config.transliteration_provider = self._llm_provider_combo.currentData()
        if hasattr(self, "_txt_base_url"):
            self._config.transliteration_base_url = self._txt_base_url.text().strip()
        if hasattr(self, "_txt_api_key"):
            self._config.transliteration_api_key = self._txt_api_key.text().strip()
        if hasattr(self, "_txt_model"):
            self._config.transliteration_model = self._txt_model.text().strip()
        if hasattr(self, "_txt_system_prompt"):
            self._config.transliteration_system_prompt = self._txt_system_prompt.toPlainText().strip()

        # Apply pending to config for voice, speed, theme, and system accent
        if "speaker" in self._pending:
            self._config.speaker = self._pending["speaker"]
        if "speed" in self._pending:
            self._config.speed = self._pending["speed"]
        if "theme" in self._pending:
            self._config.theme = self._pending["theme"]
        if "use_system_accent" in self._pending:
            self._config.use_system_accent = self._pending["use_system_accent"]

        self._config.save()
        self._pending.clear()
        self.settings_saved.emit()
        self.accept()

    def _on_reset(self) -> None:
        reply = QMessageBox.question(
            self,
            "Сброс настроек",
            "Сбросить все настройки к значениям по умолчанию?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._config.reset_to_defaults()
            self._pending.clear()
            self.settings_saved.emit()
            self.accept()
