"""Carte de statistique du tableau de bord (vitesse, réussites, espace disque, ETA)."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from tikvault.ui.icons import icon


class StatCard(QFrame):
    """Petite carte : icône, titre, valeur, sous-titre, avec apparition en fondu."""

    def __init__(
        self,
        title: str,
        icon_name: str,
        *,
        value: str = "—",
        subtitle: str = "",
        accent: str = "#ff2e63",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self._accent = accent
        self._icon_name = icon_name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(36, 36)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_icon()

        text_box = QVBoxLayout()
        text_box.setSpacing(2)

        self._title = QLabel(title.upper(), self)
        self._title.setProperty("cardTitle", True)

        self._value = QLabel(value, self)
        self._value.setProperty("cardValue", True)

        self._subtitle = QLabel(subtitle, self)
        self._subtitle.setProperty("muted", True)
        self._subtitle.setWordWrap(True)

        text_box.addWidget(self._title)
        text_box.addWidget(self._value)
        text_box.addWidget(self._subtitle)
        text_box.addStretch(1)

        layout.addWidget(self._icon_label)
        layout.addLayout(text_box, 1)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 55))
        self.setGraphicsEffect(shadow)

        self.setMinimumHeight(86)

    # --- API --------------------------------------------------------------
    def set_value(self, value: str, *, subtitle: str | None = None) -> None:
        self._value.setText(value)
        if subtitle is not None:
            self._subtitle.setText(subtitle)

    def value_text(self) -> str:
        return self._value.text()

    def set_accent(self, color: str) -> None:
        self._accent = color
        self._apply_icon()

    def fade_in(self, delay_ms: int = 0) -> None:
        """Apparition progressive (utilisée à l'affichage d'une page)."""
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(320)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.start()
        self._fade = (effect, animation, delay_ms)  # garde les références vivantes

    # --- Interne ----------------------------------------------------------
    def _apply_icon(self) -> None:
        self._icon_label.setPixmap(icon(self._icon_name, self._accent, 64).pixmap(26, 26))
