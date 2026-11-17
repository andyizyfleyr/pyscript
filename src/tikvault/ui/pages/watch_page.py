"""Page Veille : comptes surveillés, téléchargement automatique des nouveautés."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tikvault.domain.models import WatchedAccount
from tikvault.ui.icons import icon

HEADERS = ("Compte", "Intervalle", "Actif", "Dernière vérification", "Vidéos mémorisées")


class WatchPage(QWidget):
    """Gestion des comptes surveillés."""

    add_requested = Signal(str, str)  # pseudo, intervalle (minutes)
    remove_requested = Signal(str)
    toggle_requested = Signal(str, bool)
    check_now_requested = Signal()

    def __init__(self, *, accent: str = "#ff2e63", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("Veille de comptes", self)
        title.setProperty("pageTitle", True)
        root.addWidget(title)

        intro = QLabel(
            "Chaque compte surveillé est vérifié à intervalle régulier : les nouvelles vidéos "
            "partent automatiquement dans la file de téléchargement, sans doublon.",
            self,
        )
        intro.setProperty("muted", True)
        intro.setWordWrap(True)
        root.addWidget(intro)

        root.addWidget(self._build_form())

        self.table = QTableWidget(self)
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(HEADERS)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        refresh = QPushButton(icon("refresh", "#00d2d3", 40), "Vérifier maintenant", self)
        refresh.clicked.connect(self.check_now_requested)
        remove = QPushButton(icon("trash", "#ef4444", 40), "Supprimer la sélection", self)
        remove.setProperty("danger", True)
        remove.clicked.connect(self._remove_selected)
        actions.addWidget(refresh)
        actions.addWidget(remove)
        actions.addStretch(1)
        root.addLayout(actions)

    # ------------------------------------------------------------------
    def _build_form(self) -> QGroupBox:
        box = QGroupBox("Ajouter un compte", self)
        row = QHBoxLayout(box)
        row.setContentsMargins(14, 14, 14, 14)

        self.username = QLineEdit(box)
        self.username.setPlaceholderText("@utilisateur")
        self.username.returnPressed.connect(self._add)

        self.interval = QSpinBox(box)
        self.interval.setRange(1, 1440)
        self.interval.setValue(30)
        self.interval.setSuffix(" min")

        add = QPushButton(icon("plus", "#ffffff", 40), "Surveiller", box)
        add.setProperty("primary", True)
        add.clicked.connect(self._add)

        row.addWidget(QLabel("Compte", box))
        row.addWidget(self.username, 1)
        row.addWidget(QLabel("Toutes les", box))
        row.addWidget(self.interval)
        row.addWidget(add)
        return box

    def _add(self) -> None:
        username = self.username.text().strip().lstrip("@")
        if not username:
            return
        self.add_requested.emit(username, str(self.interval.value()))
        self.username.clear()

    def _remove_selected(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            item = self.table.item(row, 0)
            if item is not None:
                self.remove_requested.emit(item.text().lstrip("@"))

    # ------------------------------------------------------------------
    def set_accounts(self, accounts: list[WatchedAccount]) -> None:
        self.table.setRowCount(0)
        for account in accounts:
            row = self.table.rowCount()
            self.table.insertRow(row)

            name = QTableWidgetItem(f"@{account.username}")
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(f"{account.interval_minutes} min"))

            toggle = QCheckBox(self.table)
            toggle.setChecked(account.enabled)
            toggle.toggled.connect(
                lambda checked, username=account.username: self.toggle_requested.emit(
                    username, checked
                )
            )
            holder = QWidget(self.table)
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(toggle, Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(toggle)
            self.table.setCellWidget(row, 2, holder)

            last = (
                account.last_checked_at.astimezone().strftime("%d/%m/%Y %H:%M")
                if account.last_checked_at
                else "jamais"
            )
            self.table.setItem(row, 3, QTableWidgetItem(last))
            self.table.setItem(row, 4, QTableWidgetItem(str(len(account.last_video_ids))))
