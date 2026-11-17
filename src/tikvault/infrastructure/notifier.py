"""Notifications système (implémentations sans dépendance à Qt).

L'implémentation Qt (bulle de la zone de notification) vit dans
:mod:`tikvault.ui.tray` ; celles-ci servent au mode ligne de commande et aux tests.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class NullNotifier:
    """Ne fait rien (mode silencieux, tests)."""

    def notify(self, title: str, message: str, *, level: str = "info") -> None:
        return None


class ConsoleNotifier:
    """Affiche la notification dans le terminal (mode ``--headless``)."""

    def notify(self, title: str, message: str, *, level: str = "info") -> None:
        logger.log(
            level.upper() if level in {"info", "warning", "error"} else "INFO",
            "notification",
            titre=title,
            message=message,
        )


class NotifySendNotifier:
    """Utilise ``notify-send`` (Linux) ou ``osascript`` (macOS) si disponible."""

    def notify(self, title: str, message: str, *, level: str = "info") -> None:
        import shutil
        import subprocess
        import sys

        try:
            if sys.platform == "darwin":
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            elif shutil.which("notify-send"):
                subprocess.run(
                    ["notify-send", "--app-name=TikVault", title, message], check=False, timeout=5
                )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - dépend de l'OS
            logger.debug("notification_impossible", titre=title)
