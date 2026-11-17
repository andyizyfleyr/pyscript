"""Page Historique : recherche plein texte, filtres par état, export CSV."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tikvault.domain.models import DownloadTask, TaskState
from tikvault.services.disk import human_size
from tikvault.ui.icons import icon
from tikvault.ui.pages.queue_page import STATE_COLORS, STATE_LABELS

HEADERS = ("Titre", "Auteur", "Publiée le", "Vues", "État", "Taille", "Fichier", "Erreur")

_FILTERS: tuple[tuple[str, list[TaskState] | None], ...] = (
    ("Tout", None),
    ("Réussies", [TaskState.DONE]),
    ("Échecs", [TaskState.FAILED]),
    ("Ignorées", [TaskState.SKIPPED]),
    ("Annulées", [TaskState.CANCELLED]),
    (
        "En cours",
        [TaskState.QUEUED, TaskState.FETCHING, TaskState.DOWNLOADING, TaskState.POSTPROCESSING],
    ),
)


class HistoryPage(QWidget):
    """Historique consultable et exportable."""

    tasks_requested = Signal(object, str)  # liste d'états (ou None), requête
    export_requested = Signal(str)  # chemin CSV
    clear_finished_requested = Signal()

    def __init__(self, *, accent: str = "#ff2e63", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addLayout(self._build_header())
        root.addLayout(self._build_filters())

        self.table = QTableWidget(self)
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(HEADERS)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(6, 260)
        root.addWidget(self.table, 1)

        self._summary = QLabel("", self)
        self._summary.setProperty("muted", True)
        root.addWidget(self._summary)

        # Recherche « en cours de frappe » : on attend une pause avant d'interroger la base.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._emit_request)

    # ------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        title = QLabel("Historique", self)
        title.setProperty("pageTitle", True)
        bar.addWidget(title)
        bar.addStretch(1)

        refresh = QPushButton(icon("refresh", "#00d2d3", 40), "Actualiser", self)
        refresh.clicked.connect(self.refresh)
        bar.addWidget(refresh)

        export = QPushButton(icon("export", "#ffffff", 40), "Exporter en CSV", self)
        export.setProperty("primary", True)
        export.clicked.connect(self._export)
        bar.addWidget(export)

        clear = QPushButton(icon("trash", "#ef4444", 40), "Purger les terminées", self)
        clear.setProperty("danger", True)
        clear.clicked.connect(self.clear_finished_requested)
        bar.addWidget(clear)
        return bar

    def _build_filters(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Rechercher un titre, un auteur, une URL, un identifiant…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._debounce.start())

        self.state_filter = QComboBox(self)
        for label, _states in _FILTERS:
            self.state_filter.addItem(label)
        self.state_filter.currentIndexChanged.connect(self._emit_request)

        icon_label = QLabel(self)
        icon_label.setPixmap(icon("search", "#8b93a7", 40).pixmap(18, 18))

        bar.addWidget(icon_label)
        bar.addWidget(self.search, 1)
        bar.addWidget(QLabel("État", self))
        bar.addWidget(self.state_filter)
        return bar

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._emit_request()

    def _emit_request(self) -> None:
        index = self.state_filter.currentIndex()
        states = _FILTERS[index][1] if 0 <= index < len(_FILTERS) else None
        self.tasks_requested.emit(
            [state.value for state in states] if states else None, self.search.text().strip()
        )

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le rapport", "tikvault-rapport.csv", "Fichier CSV (*.csv)"
        )
        if path:
            self.export_requested.emit(path)

    # ------------------------------------------------------------------
    def set_tasks(self, tasks: list[DownloadTask]) -> None:
        """Affiche le résultat d'une requête d'historique."""
        self.table.setRowCount(0)
        for task in tasks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            meta = task.meta
            cells = (
                meta.display_title if meta else task.url,
                (meta.uploader_id or meta.uploader) if meta else "—",
                meta.upload_date.isoformat() if meta and meta.upload_date else "—",
                f"{meta.view_count:,}".replace(",", " ") if meta and meta.view_count else "—",
                STATE_LABELS.get(task.state, task.state.value),
                human_size(task.file_size) if task.file_size else "—",
                task.file_path or "—",
                task.error_message or task.skip_reason or "",
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if column == 4:
                    item.setForeground(QColor(STATE_COLORS.get(task.state, "#8b93a7")))
                if column == 6:
                    item.setToolTip(str(text))
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)

        self._summary.setText(
            f"{len(tasks)} ligne(s) affichée(s)"
            + (" · affinez la recherche si le résultat est incomplet" if len(tasks) >= 2000 else "")
        )
