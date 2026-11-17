"""Zone de notification (tray) et notifications système natives via Qt."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from tikvault.ui.icons import icon


class TrayNotifier:
    """Implémente le port ``Notifier`` avec la zone de notification Qt."""

    def __init__(self, parent: QWidget, *, accent: str = "#ff2e63") -> None:
        self._icon = QSystemTrayIcon(icon("logo", accent, 64), parent)
        self._available = QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def available(self) -> bool:
        return self._available

    def set_icon(self, icon_name: str, color: str) -> None:
        self._icon.setIcon(icon(icon_name, color, 64))

    def show(self) -> None:
        if self._available:
            self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def set_menu(self, menu: QMenu) -> None:
        self._icon.setContextMenu(menu)

    def set_tooltip(self, text: str) -> None:
        self._icon.setToolTip(text)

    def notify(self, title: str, message: str, *, level: str = "info") -> None:
        """Notification système (port ``Notifier``)."""
        if not self._available:
            return
        severity = {
            "info": QSystemTrayIcon.MessageIcon.Information,
            "warning": QSystemTrayIcon.MessageIcon.Warning,
            "error": QSystemTrayIcon.MessageIcon.Critical,
        }.get(level, QSystemTrayIcon.MessageIcon.Information)
        self._icon.showMessage(title, message, severity, 6000)

    def on_activated(self, callback: Callable[[object], None]) -> None:
        self._icon.activated.connect(callback)


def build_tray_menu(
    parent: QWidget,
    *,
    on_show: Callable[[], None],
    on_pause: Callable[[], None],
    on_quit: Callable[[], None],
) -> QMenu:
    """Menu contextuel de la zone de notification."""
    menu = QMenu(parent)
    show_action = QAction(icon("eye", "#8b93a7", 40), "Ouvrir TikVault", menu)
    show_action.triggered.connect(on_show)
    pause_action = QAction(icon("pause", "#f59e0b", 40), "Pause / Reprendre", menu)
    pause_action.triggered.connect(on_pause)
    quit_action = QAction(icon("stop", "#ef4444", 40), "Quitter", menu)
    quit_action.triggered.connect(on_quit)
    menu.addAction(show_action)
    menu.addAction(pause_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    return menu


__all__ = ["TrayNotifier", "build_tray_menu"]
