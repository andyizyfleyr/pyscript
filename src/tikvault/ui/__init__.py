"""Couche interface graphique (PySide6)."""

from __future__ import annotations

from tikvault.ui.engine_bridge import EngineBridge
from tikvault.ui.main_window import MainWindow
from tikvault.ui.theme import apply_palette, dark_palette, light_palette, palette_for
from tikvault.ui.tray import TrayNotifier

__all__ = [
    "EngineBridge",
    "MainWindow",
    "TrayNotifier",
    "apply_palette",
    "dark_palette",
    "light_palette",
    "palette_for",
]
