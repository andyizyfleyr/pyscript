"""Section repliable : un titre cliquable qui montre/masque son contenu.

Pierre angulaire de la v2 : les options avancées restent accessibles mais
n'encombrent plus l'écran — seuls les réglages essentiels sont visibles d'emblée.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """En-tête fléché + contenu masquable (replié par défaut)."""

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._content = content

        self._header = QToolButton(self)
        self._header.setText(f"  {title}")
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setProperty("sectionHeader", True)
        self._header.setAutoRaise(True)
        self._header.toggled.connect(self._on_toggled)

        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setProperty("muted", True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)
        layout.addWidget(self._header)
        layout.addWidget(line)
        layout.addWidget(content)

        content.setVisible(expanded)

    def _on_toggled(self, expanded: bool) -> None:
        self._content.setVisible(expanded)
        self._header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def expand(self) -> None:
        self._header.setChecked(True)


__all__ = ["CollapsibleSection"]
