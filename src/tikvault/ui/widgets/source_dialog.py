"""Boîte de dialogue d'ajout de sources (liens en vrac, fichier, compte, hashtag, son)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tikvault.services.url_parser import SourceParser
from tikvault.ui.icons import icon


class SourceDialog(QDialog):
    """Saisie des sources + options rapides du lot."""

    def __init__(self, parent: QWidget | None = None, *, accent: str = "#ff2e63") -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajouter des sources")
        self.setModal(True)
        self.resize(660, 560)
        self.setWindowIcon(icon("plus", accent, 64))
        self._parser = SourceParser()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        root.addWidget(self._build_tabs())
        root.addWidget(self._build_quick_options())
        root.addWidget(self._build_preview())
        root.addWidget(self._build_buttons())

    # ------------------------------------------------------------------
    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget(self)

        # --- Liens en vrac ---
        links_page = QWidget(tabs)
        links_layout = QVBoxLayout(links_page)
        self.links_input = QPlainTextEdit(links_page)
        self.links_input.setPlaceholderText(
            "Collez vos liens TikTok ici, un par ligne ou en vrac.\n"
            "Les raccourcis (vm.tiktok.com/…), les profils (@pseudo) et les hashtags (#tag)\n"
            "sont reconnus automatiquement. Les doublons sont ignorés."
        )
        self.links_input.textChanged.connect(self._refresh_preview)
        links_layout.addWidget(self.links_input)
        tabs.addTab(links_page, icon("link", "#8b93a7", 40), "Liens")

        # --- Fichier ---
        file_page = QWidget(tabs)
        file_layout = QVBoxLayout(file_page)
        file_row = QHBoxLayout()
        self.file_path = QLineEdit(file_page)
        self.file_path.setPlaceholderText("Chemin d'un fichier .txt ou .csv")
        self.file_path.setReadOnly(True)
        browse = QPushButton("Parcourir…", file_page)
        browse.clicked.connect(self._choose_file)
        file_row.addWidget(self.file_path, 1)
        file_row.addWidget(browse)
        file_layout.addLayout(file_row)
        hint = QLabel(
            "Format .txt : une entrée par ligne (les lignes commençant par « # » sont ignorées).\n"
            "Format .csv : colonne « url », « lien » ou « link », sinon balayage de toutes les colonnes.",
            file_page,
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        file_layout.addWidget(hint)
        file_layout.addStretch(1)
        tabs.addTab(file_page, icon("file", "#8b93a7", 40), "Fichier")

        # --- Compte / hashtag / son ---
        entity_page = QWidget(tabs)
        form = QFormLayout(entity_page)
        form.setContentsMargins(6, 12, 6, 6)
        self.profile_input = QLineEdit(entity_page)
        self.profile_input.setPlaceholderText("@utilisateur")
        self.hashtag_input = QLineEdit(entity_page)
        self.hashtag_input.setPlaceholderText("#danse")
        self.sound_input = QLineEdit(entity_page)
        self.sound_input.setPlaceholderText(
            "https://www.tiktok.com/music/… ou l'identifiant du son"
        )
        form.addRow("Profil complet", self.profile_input)
        form.addRow("Hashtag", self.hashtag_input)
        form.addRow("Son / musique", self.sound_input)
        tabs.addTab(entity_page, icon("user", "#8b93a7", 40), "Compte / tag / son")

        return tabs

    def _build_quick_options(self) -> QGroupBox:
        box = QGroupBox("Options rapides du lot", self)
        layout = QFormLayout(box)
        layout.setContentsMargins(12, 14, 12, 12)

        self.audio_only = QCheckBox("Extraire uniquement l'audio (nécessite ffmpeg)", box)
        self.max_videos = QSpinBox(box)
        self.max_videos.setRange(0, 10_000)
        self.max_videos.setValue(0)
        self.max_videos.setSpecialValueText("illimité")
        self.min_views = QSpinBox(box)
        self.min_views.setRange(0, 100_000_000)
        self.min_views.setSingleStep(1000)
        self.min_views.setValue(0)
        self.min_views.setSpecialValueText("aucun minimum")

        date_row = QHBoxLayout()
        self.date_from = QDateEdit(box)
        self.date_to = QDateEdit(box)
        for widget, days in ((self.date_from, 365), (self.date_to, 0)):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("dd/MM/yyyy")
            widget.setDate(QDate.currentDate().addDays(-days))
        self.date_enabled = QCheckBox("Restreindre aux dates", box)
        date_row.addWidget(self.date_enabled)
        date_row.addWidget(self.date_from)
        date_row.addWidget(QLabel("→", box))
        date_row.addWidget(self.date_to)
        date_row.addStretch(1)

        layout.addRow("Audio seul", self.audio_only)
        layout.addRow("Nombre max de vidéos", self.max_videos)
        layout.addRow("Vues minimum", self.min_views)
        layout.addRow("Période", date_row)
        return box

    def _build_preview(self) -> QFrame:
        frame = QFrame(self)
        frame.setProperty("card", True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        self.preview_label = QLabel("Aucune source détectée pour l'instant.", frame)
        self.preview_label.setProperty("muted", True)
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)
        return frame

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ajouter à la file")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setProperty("primary", True)
        ok.setIcon(icon("download", "#ffffff", 40))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        return buttons

    # ------------------------------------------------------------------
    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir un fichier",
            str(Path.home()),
            "Sources (*.txt *.csv);;Tous les fichiers (*)",
        )
        if path:
            self.file_path.setText(path)
            self._refresh_preview()

    def _collect_text(self) -> str:
        chunks = [self.links_input.toPlainText().strip()]
        for line_edit in (self.profile_input, self.hashtag_input, self.sound_input):
            value = line_edit.text().strip()
            if value:
                chunks.append(value)
        return "\n".join(chunk for chunk in chunks if chunk)

    def _refresh_preview(self) -> None:
        text = self._collect_text()
        sources = self._parser.parse_text(text) if text else []
        file_text = self.file_path.text().strip()
        file_count = 0
        if file_text and Path(file_text).exists():
            file_count = len(self._parser.parse_file(Path(file_text)))
        total = len(sources) + file_count
        if total == 0:
            self.preview_label.setText("Aucune source détectée pour l'instant.")
            return
        breakdown: dict[str, int] = {}
        for source in sources:
            breakdown[source.kind.value] = breakdown.get(source.kind.value, 0) + 1
        detail = ", ".join(f"{count} {label}" for label, count in breakdown.items())
        self.preview_label.setText(
            f"{total} source(s) reconnue(s) : {detail or '—'}"
            + (f" (+{file_count} depuis le fichier)" if file_count else "")
        )

    # --- Résultat ---------------------------------------------------------
    def text_payload(self) -> str:
        """Texte à transmettre au moteur (liens + compte/tag/son)."""
        return self._collect_text()

    def file_payload(self) -> str | None:
        path = self.file_path.text().strip()
        return path or None

    def quick_options(self) -> dict[str, object]:
        """Options rapides, fusionnées côté fenêtre avec les réglages persistés."""
        options: dict[str, object] = {
            "audio_only": self.audio_only.isChecked(),
            "max_items": self.max_videos.value() or None,
            "min_views": self.min_views.value() or None,
        }
        if self.date_enabled.isChecked():
            options["date_from"] = self.date_from.date().toPython()
            options["date_to"] = self.date_to.date().toPython()
        return options

    @staticmethod
    def default_range() -> tuple[date, date]:
        return date.today() - timedelta(days=365), date.today()


# Ré-export pratique pour les imports croisés.
__all__ = ["SourceDialog"]
