"""File d'attente : réorganisation par glisser-déposer, miniatures, barre par vidéo."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tikvault.domain.models import DownloadTask, TaskState
from tikvault.services.disk import human_size
from tikvault.ui.icons import icon
from tikvault.ui.widgets.animated_progress import AnimatedProgressBar
from tikvault.ui.widgets.thumbnail import ThumbnailLabel

HEADERS = ("", "Vidéo", "État", "Progression", "Débit / taille", "Actions")
STATE_COLORS = {
    TaskState.QUEUED: "#8b93a7",
    TaskState.FETCHING: "#00d2d3",
    TaskState.DOWNLOADING: "#ff2e63",
    TaskState.POSTPROCESSING: "#8b5cf6",
    TaskState.PAUSED: "#f59e0b",
    TaskState.DONE: "#22c55e",
    TaskState.FAILED: "#ef4444",
    TaskState.CANCELLED: "#6b7280",
    TaskState.SKIPPED: "#64748b",
}
STATE_LABELS = {
    TaskState.QUEUED: "en file",
    TaskState.FETCHING: "résolution",
    TaskState.DOWNLOADING: "téléchargement",
    TaskState.POSTPROCESSING: "finalisation",
    TaskState.PAUSED: "en pause",
    TaskState.DONE: "terminé",
    TaskState.FAILED: "échec",
    TaskState.CANCELLED: "annulé",
    TaskState.SKIPPED: "ignoré",
}


class QueueTable(QTableWidget):
    """Tableau avec glisser-déposer interne pour changer l'ordre des tâches."""

    order_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(len(HEADERS))
        self.setHorizontalHeaderLabels(HEADERS)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(0, 84)
        self.setColumnWidth(3, 200)
        self.setColumnWidth(5, 118)
        self.verticalHeader().setDefaultSectionSize(56)

        # Après un déplacement, on reconstruit : les widgets de cellule ne suivent pas
        # automatiquement le déplacement des lignes dans Qt.
        self.model().rowsMoved.connect(self._on_rows_moved)

    def _on_rows_moved(self, *args: object) -> None:
        order: list[str] = []
        for row in range(self.rowCount()):
            item = self.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole):
                order.append(str(item.data(Qt.ItemDataRole.UserRole)))
        if order:
            self.order_changed.emit(order)


class QueuePage(QWidget):
    """Page « File d'attente »."""

    add_requested = Signal()
    pause_all_requested = Signal()
    resume_all_requested = Signal()
    cancel_all_requested = Signal()
    retry_requested = Signal()
    pause_task_requested = Signal(str)
    resume_task_requested = Signal(str)
    cancel_task_requested = Signal(str)
    reorder_requested = Signal(list)
    thumbnail_requested = Signal(str)

    def __init__(
        self, *, accent: str = "#ff2e63", compact: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._accent = accent
        self._compact = compact
        self._tasks: dict[str, DownloadTask] = {}
        self._order: list[str] = []
        self._rebuilding = False

        root = QVBoxLayout(self)
        # En mode compact (page Télécharger de la v2), la page hôte gère déjà les marges.
        if compact:
            root.setContentsMargins(0, 0, 0, 0)
        else:
            root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        root.addLayout(self._build_toolbar())

        self.table = QueueTable(self)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.order_changed.connect(self._on_order_changed)
        root.addWidget(self.table, 1)

        self._hint = QLabel(
            "Astuce : glissez une ligne pour changer l'ordre de téléchargement.", self
        )
        self._hint.setProperty("muted", True)
        root.addWidget(self._hint)

    # ------------------------------------------------------------------
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        if self._compact:
            # Mode intégré (page Télécharger) : un simple titre de section.
            section = QLabel("Téléchargements", self)
            section.setProperty("cardTitle", True)
            bar.addWidget(section)
        else:
            title = QLabel("File d'attente", self)
            title.setProperty("pageTitle", True)
            bar.addWidget(title)
        self._counter = QLabel("", self)
        self._counter.setProperty("muted", True)
        bar.addWidget(self._counter)
        bar.addStretch(1)

        self._pause = QPushButton(icon("pause", "#f59e0b", 40), "Pause", self)
        self._pause.setToolTip("Mettre tous les téléchargements en pause")
        self._pause.clicked.connect(self.pause_all_requested)

        self._resume = QPushButton(icon("play", "#22c55e", 40), "Reprendre", self)
        self._resume.setToolTip("Reprendre tous les téléchargements")
        self._resume.clicked.connect(self.resume_all_requested)

        self._retry = QPushButton(icon("refresh", "#00d2d3", 40), "Relancer les échecs", self)
        self._retry.setToolTip("Remettre en file toutes les vidéos en échec")
        self._retry.clicked.connect(self.retry_requested)

        self._cancel = QPushButton(icon("stop", "#ef4444", 40), "Tout annuler", self)
        self._cancel.setProperty("danger", True)
        self._cancel.clicked.connect(self.cancel_all_requested)

        buttons: tuple[QPushButton, ...]
        if self._compact:
            buttons = (self._pause, self._resume, self._retry, self._cancel)
        else:
            self._add = QPushButton(icon("plus", "#ffffff", 40), "Ajouter", self)
            self._add.setProperty("primary", True)
            self._add.clicked.connect(self.add_requested)
            buttons = (self._add, self._pause, self._resume, self._retry, self._cancel)
        for button in buttons:
            bar.addWidget(button)
        return bar

    # ------------------------------------------------------------------
    # Alimentation
    # ------------------------------------------------------------------
    def set_tasks(self, tasks: list[DownloadTask]) -> None:
        """Remplace tout le contenu (chargement initial ou rafraîchissement complet)."""
        self._tasks = {task.id: task for task in tasks}
        self._order = [task.id for task in tasks]
        self.rebuild()

    def add_task(self, task: DownloadTask) -> None:
        if task.id in self._tasks:
            self._tasks[task.id] = task
            self._update_row(task)
            return
        self._tasks[task.id] = task
        self._order.append(task.id)
        self._append_row(task)
        self._refresh_counter()

    def update_state(self, task_id: str, state: str, message: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        try:
            task.state = TaskState(state)
        except ValueError:
            return
        if message:
            task.error_message = message
        self._update_row(task)

    def update_meta(self, task_id: str, meta: object) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.meta = meta  # type: ignore[assignment]
        self._update_row(task)
        thumbnail = getattr(meta, "thumbnail", None)
        if thumbnail:
            self.thumbnail_requested.emit(thumbnail)

    def update_progress(self, task_id: str, ratio: float, speed: float, phase: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.progress = max(0.0, ratio)
        task.speed_bps = speed
        row = self._row_of(task_id)
        if row < 0:
            return
        bar = self.table.cellWidget(row, 3)
        if isinstance(bar, AnimatedProgressBar):
            if ratio < 0:
                bar.set_label(phase)
            else:
                bar.set_value(ratio)
                bar.set_label(f"{ratio * 100:.0f} % · {phase}")
        cell = self.table.item(row, 4)
        if cell is not None:
            cell.setText(f"{speed / 1024:,.0f} Ko/s".replace(",", " ") if speed else phase)

    def set_thumbnail(self, url: str, path: str) -> None:
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if isinstance(widget, ThumbnailLabel) and widget.source_url == url:
                widget.load_from(path, url=url)
                return
        # Sinon on cherche la tâche correspondante par URL de miniature.
        for task_id, task in self._tasks.items():
            if task.meta is not None and task.meta.thumbnail == url:
                row = self._row_of(task_id)
                widget = self.table.cellWidget(row, 0)
                if isinstance(widget, ThumbnailLabel):
                    widget.load_from(path, url=url)
                return

    # ------------------------------------------------------------------
    # Construction des lignes
    # ------------------------------------------------------------------
    def rebuild(self) -> None:
        self._rebuilding = True
        self.table.setRowCount(0)
        for task_id in self._order:
            task = self._tasks.get(task_id)
            if task is not None:
                self._append_row(task)
        self._rebuilding = False
        self._refresh_counter()

    def _append_row(self, task: DownloadTask) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        thumbnail = ThumbnailLabel(size=72, parent=self.table)
        if task.meta is not None and task.meta.thumbnail:
            self.thumbnail_requested.emit(task.meta.thumbnail)
        self.table.setCellWidget(row, 0, thumbnail)

        title = QTableWidgetItem(self._display_title(task))
        title.setData(Qt.ItemDataRole.UserRole, task.id)
        title.setToolTip(task.url)
        self.table.setItem(row, 1, title)

        state_item = QTableWidgetItem(STATE_LABELS.get(task.state, task.state.value))
        state_item.setForeground(QColor(STATE_COLORS.get(task.state, "#8b93a7")))
        state_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 2, state_item)

        bar = AnimatedProgressBar(self.table, accent=self._accent)
        bar.set_value(max(0.0, task.progress), animate=False)
        bar.set_label(f"{task.percent} %" if task.state is not TaskState.QUEUED else "en attente")
        bar.set_state(self._bar_state(task.state))
        self.table.setCellWidget(row, 3, bar)

        size_item = QTableWidgetItem(human_size(task.file_size) if task.file_size else "—")
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 4, size_item)

        self.table.setCellWidget(row, 5, self._action_widget(task))

    def _update_row(self, task: DownloadTask) -> None:
        row = self._row_of(task.id)
        if row < 0:
            return
        title_item = self.table.item(row, 1)
        state_item = self.table.item(row, 2)
        if title_item is None or state_item is None:
            return
        title_item.setText(self._display_title(task))

        state_item.setText(STATE_LABELS.get(task.state, task.state.value))
        state_item.setForeground(QColor(STATE_COLORS.get(task.state, "#8b93a7")))
        if task.state is TaskState.FAILED and task.error_message:
            state_item.setToolTip(task.error_message)
        elif task.state is TaskState.SKIPPED and task.skip_reason:
            state_item.setToolTip(task.skip_reason)

        bar = self.table.cellWidget(row, 3)
        if isinstance(bar, AnimatedProgressBar):
            bar.set_state(self._bar_state(task.state))
            if task.state is TaskState.DONE:
                bar.set_value(1.0)
                bar.set_label("terminé")
            elif task.state in {TaskState.QUEUED, TaskState.PAUSED}:
                bar.set_label("en attente" if task.state is TaskState.QUEUED else "en pause")

        size_item = self.table.item(row, 4)
        if size_item is not None and task.file_size:
            size_item.setText(human_size(task.file_size))

        self.table.setCellWidget(row, 5, self._action_widget(task))
        self._refresh_counter()

    def _action_widget(self, task: DownloadTask) -> QWidget:
        container = QWidget(self.table)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)

        running = task.state.is_active or task.state is TaskState.QUEUED
        pause = self._tool_button(
            "pause" if running else "play", "#f59e0b", "Mettre en pause / reprendre"
        )
        pause.clicked.connect(
            lambda _=False, tid=task.id, active=running: (
                self.pause_task_requested.emit(tid)
                if active
                else self.resume_task_requested.emit(tid)
            )
        )
        cancel = self._tool_button("stop", "#ef4444", "Annuler")
        cancel.clicked.connect(lambda _=False, tid=task.id: self.cancel_task_requested.emit(tid))
        cancel.setEnabled(not task.state.is_terminal)

        layout.addWidget(pause)
        layout.addWidget(cancel)
        layout.addStretch(1)
        return container

    def _tool_button(self, icon_name: str, color: str, tooltip: str) -> QToolButton:
        button = QToolButton(self.table)
        button.setIcon(icon(icon_name, color, 40))
        button.setIconSize(button.iconSize().__class__(16, 16))
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _bar_state(state: TaskState) -> str:
        if state is TaskState.DONE:
            return "done"
        if state in {TaskState.FAILED, TaskState.CANCELLED}:
            return "error"
        return "normal"

    @staticmethod
    def _display_title(task: DownloadTask) -> str:
        if task.meta is not None:
            author = task.meta.uploader_id or task.meta.uploader
            suffix = f" — @{author}" if author else ""
            return f"{task.meta.display_title}{suffix}"
        return task.url

    # ------------------------------------------------------------------
    def _row_of(self, task_id: str) -> int:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == task_id:
                return row
        return -1

    def _refresh_counter(self) -> None:
        total = len(self._tasks)
        active = sum(
            1
            for task in self._tasks.values()
            if task.state.is_active or task.state is TaskState.QUEUED
        )
        self._counter.setText(f"· {active} active(s) / {total}")

    def _on_selection(self) -> None:
        return None

    def _on_order_changed(self, ordered_ids: list[str]) -> None:
        if self._rebuilding:
            return
        self._order = ordered_ids
        self.reorder_requested.emit(ordered_ids)

    def _show_menu(self, position: QPoint) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            return
        task_ids: list[str] = []
        for row in rows:
            item = self.table.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole):
                task_ids.append(str(item.data(Qt.ItemDataRole.UserRole)))
        menu = QMenu(self)
        pause_action = QAction(icon("pause", "#f59e0b", 40), "Mettre en pause", menu)
        pause_action.triggered.connect(lambda: self._emit_for(self.pause_task_requested, task_ids))
        resume_action = QAction(icon("play", "#22c55e", 40), "Reprendre", menu)
        resume_action.triggered.connect(
            lambda: self._emit_for(self.resume_task_requested, task_ids)
        )
        cancel_action = QAction(icon("stop", "#ef4444", 40), "Annuler", menu)
        cancel_action.triggered.connect(
            lambda: self._emit_for(self.cancel_task_requested, task_ids)
        )
        menu.addAction(pause_action)
        menu.addAction(resume_action)
        menu.addSeparator()
        menu.addAction(cancel_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _emit_for(self, signal: object, task_ids: list[str]) -> None:
        """Émet un signal pour chaque tâche sélectionnée (menu contextuel)."""
        for task_id in task_ids:
            signal.emit(task_id)  # type: ignore[attr-defined]

    def clear_finished_rows(self) -> None:
        """Retire les tâches terminées de l'affichage."""
        self._order = [
            task_id
            for task_id in self._order
            if self._tasks[task_id].state in {TaskState.QUEUED, TaskState.PAUSED}
            or self._tasks[task_id].state.is_active
        ]
        self.rebuild()


__all__ = ["QFrame", "QueuePage", "QueueTable"]
