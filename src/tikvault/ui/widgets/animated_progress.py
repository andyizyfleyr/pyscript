"""Barre de progression animée (interpolation fluide plutôt que sauts brusques)."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class AnimatedProgressBar(QWidget):
    """Barre dessinée à la main, avec animation et état visuel (normal / fini / erreur)."""

    value_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None, *, accent: str = "#ff2e63") -> None:
        super().__init__(parent)
        self._value = 0.0
        self._display = 0.0
        self._accent = QColor(accent)
        self._state = "normal"
        self._label = ""
        self._animation = QPropertyAnimation(self, b"displayValue", self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setMinimumHeight(10)
        self.setMaximumHeight(14)

    # --- Propriété animée -------------------------------------------------
    def _get_display(self) -> float:
        return self._display

    def _set_display(self, value: float) -> None:
        self._display = value
        self.update()

    display_value = Property(float, _get_display, _set_display)

    # --- API --------------------------------------------------------------
    def set_value(self, value: float, *, animate: bool = True) -> None:
        """Positionne la progression (0.0 → 1.0)."""
        value = max(0.0, min(1.0, value))
        if abs(value - self._value) < 1e-4:
            return
        self._value = value
        self.value_changed.emit(value)
        self._animation.stop()
        if animate:
            self._animation.setStartValue(self._display)
            self._animation.setEndValue(value)
            self._animation.start()
        else:
            self._display = value
            self.update()

    def set_state(self, state: str) -> None:
        """``normal``, ``done`` ou ``error`` — change la couleur de remplissage."""
        if state != self._state:
            self._state = state
            self.update()

    def set_label(self, text: str) -> None:
        self._label = text
        self.update()

    def set_accent(self, color: str) -> None:
        self._accent = QColor(color)
        self.update()

    def value(self) -> float:
        return self._value

    # --- Peinture ---------------------------------------------------------
    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        bounds = QRect(0, 0, self.width(), self.height())
        radius = self.height() / 2

        # Fond
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._track_color())
        painter.drawRoundedRect(bounds, radius, radius)

        # Remplissage
        width = int(bounds.width() * self._display)
        if width > 2:
            painter.setBrush(self._fill_color())
            painter.drawRoundedRect(QRect(0, 0, width, self.height()), radius, radius)

        # Libellé centré
        if self._label:
            painter.setPen(QPen(self._text_color()))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, self._label)
        painter.end()

    def _fill_color(self) -> QColor:
        if self._state == "done":
            return QColor("#22c55e")
        if self._state == "error":
            return QColor("#ef4444")
        return self._accent

    def _track_color(self) -> QColor:
        base = self.palette().window().color()
        return QColor(
            min(255, base.red() + 22), min(255, base.green() + 26), min(255, base.blue() + 34)
        )

    def _text_color(self) -> QColor:
        text = self.palette().windowText().color()
        return QColor(text.red(), text.green(), text.blue(), 210)
