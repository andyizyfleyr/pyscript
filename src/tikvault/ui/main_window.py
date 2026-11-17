"""Fenêtre principale : navigation latérale, pages, zone de notification, barre d'état."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from tikvault import __version__
from tikvault.domain.errors import TikVaultError
from tikvault.infrastructure.paths import AppPaths
from tikvault.infrastructure.updater import current_version
from tikvault.settings import AppSettings
from tikvault.ui.engine_bridge import EngineBridge
from tikvault.ui.icons import icon
from tikvault.ui.pages.dashboard_page import DashboardPage
from tikvault.ui.pages.history_page import HistoryPage
from tikvault.ui.pages.queue_page import QueuePage
from tikvault.ui.pages.settings_page import SettingsPage
from tikvault.ui.pages.watch_page import WatchPage
from tikvault.ui.theme import apply_palette, palette_for
from tikvault.ui.tray import TrayNotifier, build_tray_menu
from tikvault.ui.widgets.source_dialog import SourceDialog

PAGES = (
    ("dashboard", "Tableau de bord", "dashboard"),
    ("queue", "File d'attente", "queue"),
    ("watch", "Veille de comptes", "user"),
    ("history", "Historique", "history"),
    ("settings", "Réglages", "settings"),
)


class MainWindow(QMainWindow):
    """Fenêtre principale de TikVault."""

    def __init__(
        self,
        *,
        bridge: EngineBridge,
        settings: AppSettings,
        paths: AppPaths,
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._settings = settings
        self._paths = paths
        self._palette = palette_for(settings.theme.value, settings.accent_color)
        self._paused = False
        self._force_close_flag = False

        self.setWindowTitle("TikVault — téléchargement massif de vidéos TikTok")
        self.setWindowIcon(icon("logo", settings.accent_color, 64))
        self.resize(1280, 820)
        self.setMinimumSize(1024, 680)

        self._build_ui()
        self._build_tray()
        self._connect_bridge()
        self._apply_theme(settings.theme.value, settings.accent_color)

        # Chargement initial différé : on attend que le moteur soit prêt.
        QTimer.singleShot(150, self._initial_load)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self._stack = QStackedWidget(central)
        self._dashboard = DashboardPage(accent=self._settings.accent_color, parent=self._stack)
        self._queue = QueuePage(accent=self._settings.accent_color, parent=self._stack)
        self._watch = WatchPage(accent=self._settings.accent_color, parent=self._stack)
        self._history = HistoryPage(accent=self._settings.accent_color, parent=self._stack)
        self._settings_page = SettingsPage(
            self._settings, accent=self._settings.accent_color, parent=self._stack
        )
        for page in (self._dashboard, self._queue, self._watch, self._history, self._settings_page):
            self._stack.addWidget(page)

        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        status = QStatusBar(self)
        self.setStatusBar(status)
        self._status_label = QLabel("Démarrage du moteur…", self)
        status.addPermanentWidget(self._status_label)

        self._connect_pages()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame(self)
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(228)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(6)

        header = QHBoxLayout()
        logo = QLabel(sidebar)
        logo.setPixmap(icon("logo", self._settings.accent_color, 64).pixmap(28, 28))
        texts = QVBoxLayout()
        texts.setSpacing(0)
        title = QLabel("TikVault", sidebar)
        title.setObjectName("SidebarTitle")
        subtitle = QLabel(f"v{__version__}", sidebar)
        subtitle.setObjectName("SidebarSubtitle")
        texts.addWidget(title)
        texts.addWidget(subtitle)
        header.addWidget(logo)
        header.addLayout(texts)
        header.addStretch(1)
        layout.addLayout(header)
        layout.addSpacing(16)

        self._nav_buttons: list[tuple[QPushButton, str]] = []
        for index, (_key, label, icon_name) in enumerate(PAGES):
            button = QPushButton(label, sidebar)
            button.setProperty("navButton", True)
            button.setCheckable(True)
            button.setIcon(icon(icon_name, "#8b93a7", 40))
            button.setIconSize(button.iconSize().__class__(18, 18))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, target=index: self._go_to(target))
            layout.addWidget(button)
            self._nav_buttons.append((button, icon_name))
        self._nav_buttons[0][0].setChecked(True)

        layout.addStretch(1)

        self._engine_label = QLabel(f"moteur yt-dlp {current_version()}", sidebar)
        self._engine_label.setObjectName("SidebarSubtitle")
        self._engine_label.setWordWrap(True)
        layout.addWidget(self._engine_label)

        self._pause_button = QPushButton("Pause générale", sidebar)
        self._pause_button.setIcon(icon("pause", "#f59e0b", 40))
        self._pause_button.clicked.connect(self._toggle_pause)
        layout.addWidget(self._pause_button)
        return sidebar

    def _build_tray(self) -> None:
        self._tray = TrayNotifier(self, accent=self._settings.accent_color)
        menu = build_tray_menu(
            self,
            on_show=self._show_window,
            on_pause=self._toggle_pause,
            on_quit=self._quit,
        )
        self._tray.set_menu(menu)
        self._tray.set_tooltip("TikVault")
        self._tray.set_icon("logo", self._settings.accent_color)
        self._tray.on_activated(self._on_tray_activated)
        if self._tray.available:
            self._tray.show()

    # ------------------------------------------------------------------
    # Câblage
    # ------------------------------------------------------------------
    def _connect_pages(self) -> None:
        self._dashboard.add_requested.connect(self._open_source_dialog)
        self._dashboard.pause_requested.connect(self._bridge.pause_all)
        self._dashboard.resume_requested.connect(self._bridge.resume_all)
        self._dashboard.cancel_requested.connect(self._confirm_cancel_all)

        self._queue.add_requested.connect(self._open_source_dialog)
        self._queue.pause_all_requested.connect(self._bridge.pause_all)
        self._queue.resume_all_requested.connect(self._bridge.resume_all)
        self._queue.cancel_all_requested.connect(self._confirm_cancel_all)
        self._queue.retry_requested.connect(self._bridge.retry_failed)
        self._queue.pause_task_requested.connect(self._bridge.pause_task)
        self._queue.resume_task_requested.connect(self._bridge.resume_task)
        self._queue.cancel_task_requested.connect(self._bridge.cancel_task)
        self._queue.reorder_requested.connect(self._bridge.reorder)
        self._queue.thumbnail_requested.connect(self._bridge.request_thumbnail)

        self._watch.add_requested.connect(self._bridge.add_watched_account)
        self._watch.remove_requested.connect(self._bridge.remove_watched_account)
        self._watch.check_now_requested.connect(self._bridge.check_now)
        self._watch.toggle_requested.connect(self._toggle_account)

        self._history.tasks_requested.connect(self._request_history)
        self._history.export_requested.connect(self._bridge.export_report)
        self._history.clear_finished_requested.connect(self._clear_finished)

        self._settings_page.save_requested.connect(self._save_settings)
        self._settings_page.check_update_requested.connect(self._bridge.check_updates)
        self._settings_page.apply_update_requested.connect(self._bridge.apply_update)

    def _connect_bridge(self) -> None:
        bridge = self._bridge
        bridge.task_added.connect(self._queue.add_task)
        bridge.task_state_changed.connect(self._queue.update_state)
        bridge.task_meta.connect(self._queue.update_meta)
        bridge.task_completed.connect(lambda task: self._queue.add_task(task))
        bridge.task_progress.connect(self._queue.update_progress)
        bridge.stats_updated.connect(self._dashboard.update_stats)
        bridge.batch_finished.connect(self._on_batch_finished)
        bridge.log_message.connect(self._on_log)
        bridge.source_resolved.connect(self._on_source_resolved)
        bridge.watcher_tick.connect(lambda account, count, message: self._on_log("info", message))
        bridge.thumbnail_ready.connect(self._queue.set_thumbnail)
        bridge.tasks_loaded.connect(self._history.set_tasks)
        bridge.watched_loaded.connect(self._watch.set_accounts)
        bridge.update_available.connect(self._on_update_available)
        bridge.engine_ready.connect(self._on_engine_ready)
        bridge.engine_error.connect(lambda message: self._on_log("error", message))

    # ------------------------------------------------------------------
    # Réactions
    # ------------------------------------------------------------------
    def _on_engine_ready(self) -> None:
        self._status_label.setText("Prêt")
        self._on_log("info", "Moteur prêt.")
        self._initial_load()
        if self._settings.auto_update_ytdlp:
            self._bridge.check_updates()

    def _initial_load(self) -> None:
        self._bridge.request_tasks()
        self._bridge.request_watched()

    def _on_log(self, level: str, message: str) -> None:
        self._dashboard.append_log(level, message)
        self._status_label.setText(message[:120])

    def _on_source_resolved(self, source: str, label: str, count: int) -> None:
        self._on_log("info", f"{label} : {count} vidéo(s) ajoutée(s) à la file.")

    def _on_batch_finished(self, done: int, failed: int, skipped: int, size: int) -> None:
        self._dashboard.notify_batch_finished(done, failed, skipped, size)
        self._tray.notify(
            "TikVault — lot terminé",
            f"{done} vidéo(s) téléchargée(s)" + (f", {failed} échec(s)" if failed else ""),
            level="warning" if failed and not done else "info",
        )
        self._refresh_history()

    def _on_update_available(self, current: str, latest: str) -> None:
        self._on_log("warning", f"yt-dlp {latest} est disponible (installé : {current}).")
        self._settings_page.set_version(f"{current} → {latest} disponible")
        self._tray.notify(
            "Mise à jour de yt-dlp",
            f"Version {latest} disponible. Page Réglages → « Mettre à jour maintenant ».",
            level="warning",
        )

    # ------------------------------------------------------------------
    # Actions utilisateur
    # ------------------------------------------------------------------
    def _open_source_dialog(self) -> None:
        dialog = SourceDialog(self, accent=self._settings.accent_color)
        if dialog.exec() != SourceDialog.DialogCode.Accepted:
            return
        options = self._settings.download_options()
        quick = dialog.quick_options()
        # Les options rapides de la boîte de dialogue priment sur les réglages globaux.
        if quick.get("audio_only"):
            options.audio_only = True
        updated_filters = options.filters.model_copy(
            update={
                "max_items": quick.get("max_items"),
                "min_views": quick.get("min_views"),
                "date_from": quick.get("date_from"),
                "date_to": quick.get("date_to"),
            }
        )
        options.filters = updated_filters

        self._bridge.add_sources_with_options(dialog.text_payload(), dialog.file_payload(), options)

    def _confirm_cancel_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "Tout annuler",
            "Annuler tous les téléchargements en cours et vider la file ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._bridge.cancel_all()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._bridge.pause_all()
            self._pause_button.setText("Reprendre")
            self._pause_button.setIcon(icon("play", "#22c55e", 40))
        else:
            self._bridge.resume_all()
            self._pause_button.setText("Pause générale")
            self._pause_button.setIcon(icon("pause", "#f59e0b", 40))

    def _toggle_account(self, username: str, enabled: bool) -> None:
        if self._bridge.container is not None:
            self._bridge.submit(self._bridge.container.watcher.set_enabled(username, enabled))

    def _request_history(self, states: object, query: str) -> None:
        self._bridge.request_tasks(states, query)  # type: ignore[arg-type]

    def _refresh_history(self) -> None:
        self._history.refresh()
        self._bridge.request_watched()

    def _clear_finished(self) -> None:
        if self._bridge.container is None:
            return
        self._bridge.submit(self._bridge.container.queue.clear_finished())
        QTimer.singleShot(400, self._refresh_history)

    def _save_settings(self) -> None:
        try:
            new_settings = self._settings_page.collect()
        except (TikVaultError, ValueError) as exc:
            QMessageBox.warning(self, "Réglages invalides", str(exc))
            return

        self._settings = new_settings
        self._bridge.apply_settings(new_settings)
        self._apply_theme(new_settings.theme.value, new_settings.accent_color)
        self._settings_page.load(new_settings)
        self._settings_page.set_status("Réglages enregistrés et appliqués.")
        self._on_log("info", "Réglages appliqués.")

    # ------------------------------------------------------------------
    # Navigation et thème
    # ------------------------------------------------------------------
    def _go_to(self, index: int) -> None:
        for position, (button, icon_name) in enumerate(self._nav_buttons):
            checked = position == index
            button.setChecked(checked)
            # L'icône d'un bouton actif passe en blanc sur fond coloré.
            button.setIcon(icon(icon_name, "#ffffff" if checked else "#8b93a7", 40))

        target = self._stack.widget(index)
        if target is None:
            return
        self._stack.setCurrentIndex(index)
        self._animate_in(target)

        if target is self._history:
            self._history.refresh()
        elif target is self._watch:
            self._bridge.request_watched()

    def _animate_in(self, widget: QWidget) -> None:
        """Fondu + léger glissement à l'affichage d'une page."""
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)

        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(widget, b"pos", self)
        slide.setDuration(220)
        origin = widget.pos()
        slide.setStartValue(QPoint(origin.x() + 24, origin.y()))
        slide.setEndValue(origin)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.start()
        self._page_animation = group  # garde la référence vivante

    def _apply_theme(self, mode: str, accent: str) -> None:
        self._palette = palette_for(mode, accent)
        app = QApplication.instance()
        if app is not None:
            apply_palette(app, self._palette)  # type: ignore[arg-type]
        # Les icônes dépendent de la couleur : on les régénère.
        for button, icon_name in self._nav_buttons:
            button.setIcon(icon(icon_name, "#ffffff" if button.isChecked() else "#8b93a7", 40))
        self.setWindowIcon(icon("logo", accent, 64))
        self._tray.set_icon("logo", accent)

    # ------------------------------------------------------------------
    # Fenêtre
    # ------------------------------------------------------------------
    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: object) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon

        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window() if self.isHidden() else self.hide()

    def closeEvent(self, event: QCloseEvent) -> None:
        downloading = (
            self._bridge.container is not None and self._bridge.container.queue.active_count > 0
        )
        if (
            self._tray.available
            and not self._force_close_flag
            and downloading
            and self._settings.confirm_on_close
        ):
            answer = QMessageBox.question(
                self,
                "Téléchargements en cours",
                "Des téléchargements sont en cours.\n\n"
                "Réduire dans la zone de notification (ils continuent) ou quitter ?\n"
                "En cas de fermeture, la file reprendra automatiquement au prochain lancement.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                event.ignore()
                self.hide()
                self._tray.notify("TikVault", "Réduit dans la zone de notification.")
                return
        event.accept()
        self._tray.hide()
        self._bridge.stop()

    def force_close(self) -> None:
        self._force_close_flag = True
        self.close()

    def _quit(self) -> None:
        self.force_close()
        QApplication.quit()


__all__ = ["MainWindow"]
