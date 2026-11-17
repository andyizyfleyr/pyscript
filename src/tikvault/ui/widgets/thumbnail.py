"""Aperçu miniature d'une vidéo (coin arrondi, image de remplacement, chargement asynchrone)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from tikvault.ui.icons import icon

_PLACEHOLDER = "file"


class ThumbnailLabel(QLabel):
    """Vignette arrondie. Le téléchargement de l'image se fait hors du thread graphique."""

    def __init__(self, size: int = 64, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._radius = 8
        self._source_url = ""
        self.setFixedSize(size, int(size * 9 / 16) if size > 48 else size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_placeholder()

    # --- API --------------------------------------------------------------
    @property
    def source_url(self) -> str:
        return self._source_url

    def set_placeholder(self) -> None:
        self._source_url = ""
        self.setPixmap(icon(_PLACEHOLDER, "#8b93a7", 64).pixmap(self._size // 2, self._size // 2))

    def load_from(self, path: str, *, url: str = "") -> None:
        """Affiche une miniature déjà présente sur le disque."""
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.set_placeholder()
            return
        self._source_url = url
        self.setPixmap(self._rounded(pixmap))

    # --- Interne ----------------------------------------------------------
    def _rounded(self, pixmap: QPixmap) -> QPixmap:
        scaled = pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Recentrage : on garde le milieu de l'image.
        offset_x = max(0, (scaled.width() - self.width()) // 2)
        offset_y = max(0, (scaled.height() - self.height()) // 2)
        scaled = scaled.copy(offset_x, offset_y, self.width(), self.height())

        result = QPixmap(scaled.size())
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, result.width(), result.height()), self._radius, self._radius
        )
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.setClipping(False)
        painter.setPen(QColor(255, 255, 255, 26))
        painter.drawPath(path)
        painter.end()
        return result
