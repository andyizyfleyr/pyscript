"""Page « Télécharger » (v2) : tout le flux principal sur un seul écran.

Philosophie : **coller → télécharger**, en deux gestes. La page regroupe :

1. une grande zone de collage (liens en vrac, ``@comptes``, ``#hashtags``) ;
2. le choix Vidéo / Audio MP3 et le bouton principal ;
3. une barre de progression globale avec un résumé d'une ligne ;
4. la liste des téléchargements (``QueuePage`` en mode compact) ;
5. un journal d'activité replié, pour qui veut les détails.

Les anciennes pages « Tableau de bord » et « File d'attente » de la v1 sont
fusionnées ici : moins de navigation, moins de concepts à comprendre.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from tikvault.domain.models import BatchStats
from tikvault.services.disk import human_duration, human_size
from tikvault.ui.icons import icon
from tikvault.ui.pages.queue_page import QueuePage
from tikvault.ui.widgets.animated_progress import AnimatedProgressBar
from tikvault.ui.widgets.collapsible import CollapsibleSection

PLACEHOLDER = (
    "Collez ici vos liens TikTok — un ou plusieurs, même noyés dans du texte.\n"
    "Fonctionne aussi avec @uncompte (profil entier) et #unhashtag.\n"
    "Exemple : https://www.tiktok.com/@chef/video/7301234567890123456"
)


class HomePage(QWidget):
    """Écran principal de la v2 : collage, lancement, suivi."""

    #: (texte collé, chemin de fichier importé ou None, audio seul ?)
    download_requested = Signal(str, object, bool)
    #: Ouvre la boîte de dialogue avancée (filtres par lot, dates…).
    advanced_requested = Signal()

    #: Journal d'activité (déclaré ici : référencé par un rappel avant sa création).
    log: QPlainTextEdit

    def __init__(self, *, accent: str = "#ff2e63", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Télécharger", self)
        title.setProperty("pageTitle", True)
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        root.addWidget(self._build_paste_card())
        root.addWidget(self._build_progress_card())

        #: Liste des téléchargements — toute la logique vit dans QueuePage.
        self.queue = QueuePage(accent=accent, compact=True, parent=self)
        root.addWidget(self.queue, 1)

        root.addWidget(self._build_log_section())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_paste_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.input = QPlainTextEdit(card)
        self.input.setPlaceholderText(PLACEHOLDER)
        self.input.setFixedHeight(96)
        self.input.setTabChangesFocus(True)
        layout.addWidget(self.input)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.video_choice = QRadioButton("Vidéo", card)
        self.video_choice.setChecked(True)
        self.audio_choice = QRadioButton("Audio (MP3)", card)
        row.addWidget(self.video_choice)
        row.addWidget(self.audio_choice)

        import_button = QPushButton(icon("file", "#8b93a7", 40), "Importer un fichier…", card)
        import_button.setToolTip("Télécharger tous les liens d'un fichier .txt ou .csv")
        import_button.clicked.connect(self._import_file)
        row.addWidget(import_button)

        advanced = QPushButton("Options avancées…", card)
        advanced.setFlat(True)
        advanced.setCursor(Qt.CursorShape.PointingHandCursor)
        advanced.setToolTip("Filtres par lot : dates, vues minimum, nombre maximum…")
        advanced.clicked.connect(self.advanced_requested)
        row.addWidget(advanced)

        row.addStretch(1)

        self.download_button = QPushButton(icon("download", "#ffffff", 40), "Télécharger", card)
        self.download_button.setProperty("primary", True)
        self.download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_button.setMinimumHeight(38)
        self.download_button.clicked.connect(self._submit)
        row.addWidget(self.download_button)

        layout.addLayout(row)
        return card

    def _build_progress_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self._summary = QLabel("Prêt. Collez des liens ci-dessus pour commencer.", card)
        self._summary.setProperty("muted", True)
        top.addWidget(self._summary, 1)
        self._percent = QLabel("", card)
        self._percent.setStyleSheet("font-weight: 700; font-size: 15px;")
        top.addWidget(self._percent)
        layout.addLayout(top)

        self.progress = AnimatedProgressBar(card, accent=self._accent)
        self.progress.setFixedHeight(12)
        layout.addWidget(self.progress)
        return card

    def _build_log_section(self) -> CollapsibleSection:
        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        self.log.setFixedHeight(130)
        self.log.setPlaceholderText("Les événements du moteur s'afficheront ici…")
        return CollapsibleSection("Journal d'activité", self.log, parent=self)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            self.input.setFocus()
            self._summary.setText("Collez d'abord au moins un lien, @compte ou #hashtag.")
            return
        self.download_requested.emit(text, None, self.audio_choice.isChecked())
        self.input.clear()
        self._summary.setText("Analyse des liens…")

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer une liste de liens",
            "",
            "Listes de liens (*.txt *.csv);;Tous les fichiers (*)",
        )
        if path:
            self.download_requested.emit("", path, self.audio_choice.isChecked())
            self._summary.setText("Analyse du fichier…")

    # ------------------------------------------------------------------
    # Alimentation par le moteur
    # ------------------------------------------------------------------
    def update_stats(self, stats: BatchStats) -> None:
        """Résumé d'une ligne — remplace les six cartes de la v1."""
        if stats.total == 0:
            self._summary.setText("Prêt. Collez des liens ci-dessus pour commencer.")
            self._percent.setText("")
            self.progress.set_value(0.0, animate=False)
            return

        parts: list[str] = [f"{stats.done} terminée(s) sur {stats.total}"]
        if stats.failed:
            parts.append(f"{stats.failed} échec(s)")
        if stats.running:
            parts.append(f"{stats.running} en cours")
        if stats.speed_bps:
            parts.append(f"{stats.speed_bps / 1024:,.0f} Ko/s".replace(",", " "))
        if stats.eta_seconds:
            parts.append(f"reste ~{human_duration(stats.eta_seconds)}")
        if stats.paused:
            parts.append("en pause")
        self._summary.setText(" · ".join(parts))

        self.progress.set_value(stats.completion)
        self._percent.setText(f"{stats.completion * 100:.0f} %")

    def append_log(self, level: str, message: str) -> None:
        prefix = {"warning": "⚠", "error": "✖", "info": "·"}.get(level, "·")
        self.log.appendPlainText(f"{prefix} {message}")

    def notify_batch_finished(self, done: int, failed: int, skipped: int, size: int) -> None:
        self.progress.set_value(1.0, animate=False)
        self.progress.set_state("error" if failed and not done else "done")
        bilan = f"Terminé : {done} vidéo(s) téléchargée(s)"
        if failed:
            bilan += f", {failed} échec(s)"
        if skipped:
            bilan += f", {skipped} ignorée(s)"
        if size:
            bilan += f" — {human_size(size)}"
        self._summary.setText(bilan + ".")
        self.append_log("info", bilan + ".")

    def reset_progress_state(self) -> None:
        self.progress.set_state("normal")


__all__ = ["HomePage"]
