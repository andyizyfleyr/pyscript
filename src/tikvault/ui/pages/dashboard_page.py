"""Tableau de bord : vitesse globale, réussites/échecs, espace disque, temps restant."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tikvault.domain.models import BatchStats
from tikvault.services.disk import human_duration, human_size
from tikvault.ui.icons import icon
from tikvault.ui.widgets.animated_progress import AnimatedProgressBar
from tikvault.ui.widgets.stat_card import StatCard


class DashboardPage(QWidget):
    """Vue d'ensemble du lot en cours + journal d'activité."""

    add_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    cancel_requested = Signal()

    #: Journal d'activité (déclaré ici : référencé par un rappel avant sa création).
    log: QPlainTextEdit

    def __init__(self, *, accent: str = "#ff2e63", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        root.addLayout(self._build_header())
        root.addWidget(self._build_progress_card())
        root.addLayout(self._build_cards())
        root.addWidget(self._build_log(), 1)

    # ------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("Tableau de bord", self)
        title.setProperty("pageTitle", True)
        header.addWidget(title)
        header.addStretch(1)

        self._add_button = QPushButton(icon("plus", "#ffffff", 40), "Ajouter des sources", self)
        self._add_button.setProperty("primary", True)
        self._add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_button.clicked.connect(self.add_requested)

        self._pause_button = QPushButton(icon("pause", "#f59e0b", 40), "Tout mettre en pause", self)
        self._pause_button.clicked.connect(self.pause_requested)

        self._resume_button = QPushButton(icon("play", "#22c55e", 40), "Reprendre", self)
        self._resume_button.clicked.connect(self.resume_requested)

        self._cancel_button = QPushButton(icon("stop", "#ef4444", 40), "Tout annuler", self)
        self._cancel_button.setProperty("danger", True)
        self._cancel_button.clicked.connect(self.cancel_requested)

        for button in (
            self._add_button,
            self._pause_button,
            self._resume_button,
            self._cancel_button,
        ):
            header.addWidget(button)
        return header

    def _build_progress_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        label = QLabel("PROGRESSION GLOBALE", card)
        label.setProperty("cardTitle", True)
        top.addWidget(label)
        top.addStretch(1)
        self._percent = QLabel("0 %", card)
        self._percent.setStyleSheet("font-weight: 700; font-size: 15px;")
        top.addWidget(self._percent)
        layout.addLayout(top)

        self.progress = AnimatedProgressBar(card, accent=self._accent)
        self.progress.setFixedHeight(14)
        layout.addWidget(self.progress)

        self._detail = QLabel("En attente de sources.", card)
        self._detail.setProperty("muted", True)
        layout.addWidget(self._detail)
        return card

    def _build_cards(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(14)
        self._speed_card = StatCard("Débit", "gauge", accent=self._accent, parent=self)
        self._done_card = StatCard("Réussies", "check", accent="#22c55e", parent=self)
        self._failed_card = StatCard("Échecs", "alert", accent="#ef4444", parent=self)
        self._eta_card = StatCard("Temps restant", "clock", accent="#00d2d3", parent=self)
        self._disk_card = StatCard("Espace utilisé", "disk", accent="#8b5cf6", parent=self)
        self._queue_card = StatCard("En file", "queue", accent="#f59e0b", parent=self)

        for column, card in enumerate(
            (
                self._speed_card,
                self._done_card,
                self._failed_card,
                self._eta_card,
                self._disk_card,
                self._queue_card,
            )
        ):
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            grid.addWidget(card, column // 3, column % 3)
        return grid

    def _build_log(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        label = QLabel("ACTIVITÉ", card)
        label.setProperty("cardTitle", True)
        header.addWidget(label)
        header.addStretch(1)
        clear = QPushButton("Vider", card)
        clear.setFixedHeight(24)
        clear.clicked.connect(lambda: self.log.clear())
        header.addWidget(clear)
        layout.addLayout(header)

        self.log = QPlainTextEdit(card)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        self.log.setPlaceholderText("Les événements du moteur s'afficheront ici…")
        layout.addWidget(self.log, 1)
        return card

    # ------------------------------------------------------------------
    def update_stats(self, stats: BatchStats) -> None:
        """Rafraîchit toutes les cartes à partir des statistiques du moteur."""
        self._speed_card.set_value(
            f"{stats.speed_bps / 1024:,.0f} Ko/s".replace(",", " ") if stats.speed_bps else "—",
            subtitle=f"{human_size(stats.bytes_downloaded)} téléchargés",
        )
        self._done_card.set_value(str(stats.done), subtitle=f"{stats.skipped} ignorée(s)")
        self._failed_card.set_value(str(stats.failed), subtitle=f"{stats.cancelled} annulée(s)")
        self._eta_card.set_value(human_duration(stats.eta_seconds), subtitle="estimation")
        self._disk_card.set_value(human_size(stats.disk_used), subtitle="dossier de destination")
        self._queue_card.set_value(
            str(stats.queued),
            subtitle=f"{stats.running} en cours" + (" · en pause" if stats.paused else ""),
        )

        percent = stats.completion * 100
        self.progress.set_value(percent / 100)
        self.progress.set_label("")
        self._percent.setText(f"{percent:.0f} %")
        self._detail.setText(
            f"{stats.done + stats.failed + stats.skipped + stats.cancelled} / {stats.total} tâche(s) traitée(s)"
            f" · {stats.running} active(s) · écoulé {human_duration(stats.elapsed_seconds)}"
        )

    def append_log(self, level: str, message: str) -> None:
        prefix = {"warning": "⚠", "error": "✖", "info": "·"}.get(level, "·")
        self.log.appendPlainText(f"{prefix} {message}")

    def notify_batch_finished(self, done: int, failed: int, skipped: int, size: int) -> None:
        self.progress.set_value(1.0, animate=False)
        self.progress.set_state("error" if failed and not done else "done")
        self.append_log(
            "info",
            f"Lot terminé : {done} réussie(s), {failed} échec(s), {skipped} ignorée(s), {human_size(size)}.",
        )

    def reset_progress_state(self) -> None:
        self.progress.set_state("normal")
