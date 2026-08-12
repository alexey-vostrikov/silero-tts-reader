"""Floating playback widget — always-on-top overlay in the bottom-right corner."""
from __future__ import annotations

from PyQt6.QtCore import (
    Qt, QPoint, QPropertyAnimation, QEasingCurve, QTimer, pyqtSignal, QRect,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QSlider, QProgressBar, QApplication, QGraphicsOpacityEffect,
)

from ..core.audio_player import SPEED_AVAILABLE


class ClickableProgressBar(QProgressBar):
    """QProgressBar that emits position_clicked(fraction) on mouse click."""

    position_clicked = pyqtSignal(float)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.width() > 0:
            fraction = max(0.0, min(1.0, event.pos().x() / self.width()))
            self.position_clicked.emit(fraction)
        super().mousePressEvent(event)


class PlaybackWidget(QWidget):
    """Translucent always-on-top playback control widget."""

    # Signals emitted by widget controls
    pause_resume_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    speed_changed = pyqtSignal(float)
    rewind_clicked = pyqtSignal(float)          # relative seconds (+5.0 or -5.0)
    seek_fraction_clicked = pyqtSignal(float)  # 0.0 to 1.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dragging = False
        self._drag_offset = QPoint()
        self._is_paused = False
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._fade_anim: QPropertyAnimation | None = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

        self._is_dark = True
        self._setup_window()
        self._setup_ui()
        self._position_bottom_right()

    def apply_theme(self, is_dark: bool = True, accent_color: str = "#6366f1") -> None:
        self._is_dark = is_dark
        self._accent_color = accent_color
        if is_dark:
            self._label.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 600;")
            self._speed_label_title.setStyleSheet("color: #94a3b8; font-size: 11px;")
            self._speed_value_label.setStyleSheet("color: #e2e8f0; font-size: 11px; min-width: 32px;")
            self._style_button(self._btn_rewind, neutral=True, is_dark=True)
            self._style_button(self._btn_pause, is_dark=True, accent_color=accent_color)
            self._style_button(self._btn_forward, neutral=True, is_dark=True)
            self._style_button(self._btn_stop, danger=True, is_dark=True)
            self._progress.setStyleSheet(f"""
                QProgressBar {{
                    background: rgba(255,255,255,0.15);
                    border-radius: 2px; border: none;
                }}
                QProgressBar::chunk {{
                    background: {accent_color};
                    border-radius: 2px;
                }}
            """)
            self._speed_slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    background: rgba(255,255,255,0.15);
                    height: 4px; border-radius: 2px;
                }}
                QSlider::handle:horizontal {{
                    background: {accent_color};
                    width: 12px; height: 12px;
                    margin: -4px 0; border-radius: 6px;
                }}
                QSlider::sub-page:horizontal {{
                    background: {accent_color}; border-radius: 2px;
                }}
            """)
        else:
            self._label.setStyleSheet("color: #0f172a; font-size: 12px; font-weight: 600;")
            self._speed_label_title.setStyleSheet("color: #64748b; font-size: 11px;")
            self._speed_value_label.setStyleSheet("color: #0f172a; font-size: 11px; min-width: 32px;")
            self._style_button(self._btn_rewind, neutral=True, is_dark=False)
            self._style_button(self._btn_pause, is_dark=False, accent_color=accent_color)
            self._style_button(self._btn_forward, neutral=True, is_dark=False)
            self._style_button(self._btn_stop, danger=True, is_dark=False)
            self._progress.setStyleSheet(f"""
                QProgressBar {{
                    background: rgba(0,0,0,0.1);
                    border-radius: 2px; border: none;
                }}
                QProgressBar::chunk {{
                    background: {accent_color};
                    border-radius: 2px;
                }}
            """)
            self._speed_slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    background: rgba(0,0,0,0.1);
                    height: 4px; border-radius: 2px;
                }}
                QSlider::handle:horizontal {{
                    background: {accent_color};
                    width: 12px; height: 12px;
                    margin: -4px 0; border-radius: 6px;
                }}
                QSlider::sub-page:horizontal {{
                    background: {accent_color}; border-radius: 2px;
                }}
            """)
        self.update()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)
        self.setFixedSize(360, 95)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(6)

        # ── Top row: label + controls ────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self._label = QLabel("🔊 Воспроизведение")
        self._label.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 600;")
        top_row.addWidget(self._label)

        top_row.addStretch()

        self._btn_rewind = QPushButton("◀◀")
        self._btn_rewind.setFixedSize(30, 30)
        self._btn_rewind.setToolTip("Назад на 5 секунд")
        self._btn_rewind.clicked.connect(lambda: self._on_rewind_clicked(-5.0))
        self._style_button(self._btn_rewind, neutral=True)
        top_row.addWidget(self._btn_rewind)

        self._btn_pause = QPushButton("⏸")
        self._btn_pause.setFixedSize(30, 30)
        self._btn_pause.setToolTip("Пауза / Продолжить")
        self._btn_pause.clicked.connect(self._on_pause_resume)
        self._style_button(self._btn_pause)
        top_row.addWidget(self._btn_pause)

        self._btn_forward = QPushButton("▶▶")
        self._btn_forward.setFixedSize(30, 30)
        self._btn_forward.setToolTip("Вперед на 5 секунд")
        self._btn_forward.clicked.connect(lambda: self._on_rewind_clicked(5.0))
        self._style_button(self._btn_forward, neutral=True)
        top_row.addWidget(self._btn_forward)

        self._btn_stop = QPushButton("⏹")
        self._btn_stop.setFixedSize(30, 30)
        self._btn_stop.setToolTip("Остановить")
        self._btn_stop.clicked.connect(self._on_stop_clicked)
        self._style_button(self._btn_stop, danger=True)
        top_row.addWidget(self._btn_stop)

        main_layout.addLayout(top_row)

        # ── Progress bar ─────────────────────────────────────────────────────
        self._progress = ClickableProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setCursor(Qt.CursorShape.PointingHandCursor)
        self._progress.setToolTip("Нажмите для перемотки")
        self._progress.position_clicked.connect(self._on_timeline_clicked)
        self._progress.setStyleSheet("""
            QProgressBar {
                background: rgba(255,255,255,0.15);
                border-radius: 2px;
                border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #6366f1, stop:1 #8b5cf6);
                border-radius: 2px;
            }
        """)
        main_layout.addWidget(self._progress)

        # ── Bottom row: speed ─────────────────────────────────────────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)

        self._speed_label_title = QLabel("Скорость:")
        self._speed_label_title.setStyleSheet("color: #94a3b8; font-size: 11px;")
        bottom_row.addWidget(self._speed_label_title)

        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(2, 12)   # 0.5x–3.0x in steps of 0.25
        self._speed_slider.setValue(4)        # 1.0x default
        self._speed_slider.setToolTip("Скорость воспроизведения")
        self._speed_slider.setEnabled(SPEED_AVAILABLE)
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        self._speed_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: rgba(255,255,255,0.15);
                height: 4px; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #6366f1;
                width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px;
            }
            QSlider::sub-page:horizontal {
                background: #6366f1; border-radius: 2px;
            }
        """)
        bottom_row.addWidget(self._speed_slider)

        self._speed_value_label = QLabel("1.0×")
        self._speed_value_label.setStyleSheet("color: #e2e8f0; font-size: 11px; min-width: 32px;")
        bottom_row.addWidget(self._speed_value_label)

        if not SPEED_AVAILABLE:
            no_rb = QLabel("(rubberband не найден)")
            no_rb.setStyleSheet("color: #f59e0b; font-size: 10px;")
            bottom_row.addWidget(no_rb)

        main_layout.addLayout(bottom_row)
        self.apply_theme(self._is_dark, getattr(self, "_accent_color", "#6366f1"))

    def _on_timeline_clicked(self, fraction: float) -> None:
        self.set_progress(fraction)
        self.seek_fraction_clicked.emit(fraction)

    def _on_rewind_clicked(self, seconds: float) -> None:
        self.rewind_clicked.emit(seconds)

    def _on_stop_clicked(self) -> None:
        self.schedule_hide(150)
        self.stop_clicked.emit()

    def _style_button(
        self,
        btn: QPushButton,
        danger: bool = False,
        neutral: bool = False,
        is_dark: bool = True,
        accent_color: str = "#6366f1",
    ) -> None:
        if neutral:
            bg = "rgba(255,255,255,0.12)" if is_dark else "rgba(0,0,0,0.06)"
            fg = "#ffffff" if is_dark else "#0f172a"
            border = "rgba(255,255,255,0.15)" if is_dark else "rgba(0,0,0,0.12)"
            hover_bg = "rgba(255,255,255,0.22)" if is_dark else "rgba(0,0,0,0.12)"
            pressed_bg = "rgba(255,255,255,0.32)" if is_dark else "rgba(0,0,0,0.2)"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg};
                    color: {fg};
                    border: 1px solid {border};
                    border-radius: 8px;
                    font-size: 10px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background: {hover_bg};
                    border-color: {border};
                    color: {fg};
                }}
                QPushButton:pressed {{
                    background: {pressed_bg};
                    color: {fg};
                }}
            """)
            return

        hover_acc = QColor(accent_color).lighter(120).name() if is_dark else QColor(accent_color).darker(115).name()
        accent = "#ef4444" if danger else accent_color
        hover = "#dc2626" if danger else hover_acc
        bg = "rgba(255,255,255,0.12)" if is_dark else "rgba(0,0,0,0.06)"
        fg = "white" if is_dark else "#0f172a"
        border = "rgba(255,255,255,0.15)" if is_dark else "rgba(0,0,0,0.12)"
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 8px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background: {accent};
                border-color: {accent};
                color: white;
            }}
            QPushButton:pressed {{
                background: {hover};
                color: white;
            }}
        """)

    # ── Positioning ───────────────────────────────────────────────────────────

    def _position_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geom: QRect = screen.availableGeometry()
        x = geom.right() - self.width() - 16
        y = geom.bottom() - self.height() - 16
        self.move(x, y)

    def restore_position(self, x: int, y: int) -> None:
        """Restore a previously saved widget position."""
        if x < 0 or y < 0:
            self._position_bottom_right()
        else:
            self.move(x, y)

    # ── Show/hide with animation ──────────────────────────────────────────────

    def fade_in(self) -> None:
        self._hide_timer.stop()
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim = None
        self._opacity_effect.setOpacity(1.0)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.show()
        self.raise_()

    def _fade_out(self) -> None:
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim = None
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(300)
        anim.setStartValue(self._opacity_effect.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.finished.connect(self._on_fade_out_finished)
        self._fade_anim = anim
        anim.start()

    def _on_fade_out_finished(self) -> None:
        if self._opacity_effect.opacity() <= 0.05:
            self.hide()

    def schedule_hide(self, delay_ms: int = 2000) -> None:
        self._hide_timer.start(delay_ms)

    # ── Public slots ──────────────────────────────────────────────────────────

    def set_progress(self, value: float) -> None:
        """Update progress bar. value is 0.0–1.0."""
        self._progress.setValue(int(value * 100))

    def set_paused(self, paused: bool) -> None:
        self._is_paused = paused
        self._btn_pause.setText("▶" if paused else "⏸")
        self._label.setText("⏸ Пауза" if paused else "🔊 Воспроизведение")

    def set_speed(self, speed: float) -> None:
        """Sync slider to a given speed (0.5–3.0)."""
        ticks = int(round(speed / 0.25))
        self._speed_slider.blockSignals(True)
        self._speed_slider.setValue(max(2, min(12, ticks)))
        self._speed_slider.blockSignals(False)
        self._speed_value_label.setText(f"{speed:.2g}×")

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_pause_resume(self) -> None:
        self.pause_resume_clicked.emit()

    def _on_speed_changed(self, tick: int) -> None:
        speed = tick * 0.25
        self._speed_value_label.setText(f"{speed:.2g}×")
        self.speed_changed.emit(speed)

    def enterEvent(self, event) -> None:
        self._hide_timer.stop()
        if self._fade_anim:
            self._fade_anim.stop()
            self._fade_anim = None
        self._opacity_effect.setOpacity(1.0)
        super().enterEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is None or child is self:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        super().mouseReleaseEvent(event)

    # ── Painting — frosted glass background ──────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 14, 14)

        if self._is_dark:
            painter.fillPath(path, QColor(24, 24, 27, 255))
            painter.setPen(QColor(255, 255, 255, 25))
        else:
            painter.fillPath(path, QColor(255, 255, 255, 255))
            painter.setPen(QColor(0, 0, 0, 40))

        painter.drawPath(path)
