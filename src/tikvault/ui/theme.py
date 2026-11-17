"""Thème de l'interface : jetons de couleur, feuilles de style Qt, thème clair/sombre.

Les couleurs sont centralisées dans :class:`Palette` ; la feuille de style est générée
à partir de ces jetons, ce qui permet de changer d'accent sans toucher au CSS.
Les transitions (fondu, barres animées) sont faites côté widgets avec
``QPropertyAnimation`` — Qt Style Sheets ne gère pas les animations.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENTS = {
    "tiktok": "#ff2e63",
    "cyan": "#00d2d3",
    "violet": "#8b5cf6",
    "vert": "#10b981",
    "ambre": "#f59e0b",
}

RADIUS = 12  # rayon d'arrondi des cartes


@dataclass(slots=True, frozen=True)
class Palette:
    """Jeu de couleurs d'un thème."""

    name: str
    window: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    success: str
    warning: str
    danger: str
    on_accent: str

    def qcolor(self, name: str) -> QColor:
        return QColor(getattr(self, name))


def dark_palette(accent: str = "#ff2e63") -> Palette:
    return Palette(
        name="sombre",
        window="#0f1115",
        surface="#171a21",
        surface_alt="#1f2430",
        border="#2a3040",
        text="#e8eaf0",
        text_muted="#8b93a7",
        accent=accent,
        accent_hover=_lighten(accent, 0.12),
        success="#22c55e",
        warning="#f59e0b",
        danger="#ef4444",
        on_accent="#ffffff",
    )


def light_palette(accent: str = "#ff2e63") -> Palette:
    return Palette(
        name="clair",
        window="#f5f6f8",
        surface="#ffffff",
        surface_alt="#eef0f5",
        border="#dfe3ec",
        text="#141821",
        text_muted="#5c6478",
        accent=accent,
        accent_hover=_darken(accent, 0.1),
        success="#16a34a",
        warning="#d97706",
        danger="#dc2626",
        on_accent="#ffffff",
    )


def _lighten(hex_color: str, amount: float) -> str:
    """Éclaircit une couleur en la rapprochant du blanc (``amount`` entre 0 et 1)."""
    color = QColor(hex_color)
    return QColor(
        min(255, round(color.red() + (255 - color.red()) * amount)),
        min(255, round(color.green() + (255 - color.green()) * amount)),
        min(255, round(color.blue() + (255 - color.blue()) * amount)),
    ).name()


def _darken(hex_color: str, amount: float) -> str:
    """Assombrit une couleur (``amount`` entre 0 et 1)."""
    color = QColor(hex_color)
    return QColor(
        round(color.red() * (1 - amount)),
        round(color.green() * (1 - amount)),
        round(color.blue() * (1 - amount)),
    ).name()


def build_stylesheet(palette: Palette) -> str:
    """Génère la feuille de style complète de l'application."""
    return f"""
* {{
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: {palette.text};
    outline: none;
}}
QMainWindow, QDialog {{ background: {palette.window}; }}
QWidget {{ background: transparent; }}

/* --- Barre latérale ------------------------------------------------- */
#Sidebar {{
    background: {palette.surface};
    border-right: 1px solid {palette.border};
}}
#SidebarTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {palette.text};
}}
#SidebarSubtitle {{ color: {palette.text_muted}; font-size: 11px; }}

QPushButton[navButton="true"] {{
    background: transparent;
    border: none;
    border-radius: {RADIUS - 2}px;
    padding: 10px 12px;
    text-align: left;
    color: {palette.text_muted};
    font-size: 13px;
    font-weight: 500;
}}
QPushButton[navButton="true"]:hover {{ background: {palette.surface_alt}; color: {palette.text}; }}
QPushButton[navButton="true"]:checked {{
    background: {palette.accent};
    color: {palette.on_accent};
    font-weight: 600;
}}

/* --- Cartes --------------------------------------------------------- */
QFrame[card="true"] {{
    background: {palette.surface};
    border: 1px solid {palette.border};
    border-radius: {RADIUS}px;
}}
QLabel[cardTitle="true"] {{
    font-size: 11px;
    font-weight: 600;
    color: {palette.text_muted};
    letter-spacing: 0.6px;
}}
QLabel[cardValue="true"] {{ font-size: 24px; font-weight: 700; }}
QLabel[pageTitle="true"] {{ font-size: 20px; font-weight: 700; }}
QLabel[muted="true"] {{ color: {palette.text_muted}; }}

/* --- Boutons -------------------------------------------------------- */
QPushButton {{
    background: {palette.surface_alt};
    border: 1px solid {palette.border};
    border-radius: {RADIUS - 3}px;
    padding: 8px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {palette.border}; }}
QPushButton:pressed {{ background: {palette.surface_alt}; }}
QPushButton:disabled {{ color: {palette.text_muted}; background: {palette.surface}; }}
QPushButton[primary="true"] {{
    background: {palette.accent};
    color: {palette.on_accent};
    border: none;
}}
QPushButton[primary="true"]:hover {{ background: {palette.accent_hover}; }}
QPushButton[danger="true"] {{ color: {palette.danger}; }}

/* --- Champs --------------------------------------------------------- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
    background: {palette.window};
    border: 1px solid {palette.border};
    border-radius: {RADIUS - 4}px;
    padding: 7px 10px;
    selection-background-color: {palette.accent};
}}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus {{
    border: 1px solid {palette.accent};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {palette.surface};
    border: 1px solid {palette.border};
    selection-background-color: {palette.accent};
    selection-color: {palette.on_accent};
}}
QCheckBox, QRadioButton {{ spacing: 8px; color: {palette.text}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}

/* --- Tableaux ------------------------------------------------------- */
QTableWidget, QTableView, QListView, QListWidget, QTreeView {{
    background: {palette.surface};
    alternate-background-color: {palette.surface};
    border: 1px solid {palette.border};
    border-radius: {RADIUS}px;
    gridline-color: {palette.border};
    selection-background-color: {palette.surface_alt};
    selection-color: {palette.text};
}}
QHeaderView::section {{
    background: {palette.surface};
    color: {palette.text_muted};
    padding: 8px;
    border: none;
    border-bottom: 1px solid {palette.border};
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {palette.surface}; border: none; }}

/* --- Barres de progression ------------------------------------------ */
QProgressBar {{
    background: {palette.surface_alt};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: {palette.text};
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: {palette.accent};
    border-radius: 6px;
}}
QProgressBar[state="done"]::chunk {{ background: {palette.success}; }}
QProgressBar[state="error"]::chunk {{ background: {palette.danger}; }}

/* --- Curseurs ------------------------------------------------------- */
QSlider::groove:horizontal {{ height: 4px; background: {palette.surface_alt}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {palette.accent};
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {palette.accent}; border-radius: 2px; }}

/* --- Onglets -------------------------------------------------------- */
QTabWidget::pane {{ border: 1px solid {palette.border}; border-radius: {RADIUS}px; }}
QTabBar::tab {{
    background: transparent; color: {palette.text_muted};
    padding: 8px 16px; border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {palette.text}; border-bottom: 2px solid {palette.accent}; }}

/* --- Divers --------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {palette.border}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {palette.text_muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {palette.border}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {palette.surface_alt}; color: {palette.text};
    border: 1px solid {palette.border}; padding: 6px; border-radius: 6px;
}}
QStatusBar {{ background: {palette.surface}; color: {palette.text_muted}; border-top: 1px solid {palette.border}; }}
QMenu {{ background: {palette.surface}; border: 1px solid {palette.border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {palette.accent}; color: {palette.on_accent}; }}
QSplitter::handle {{ background: {palette.border}; }}
"""


def apply_palette(app: QApplication, palette: Palette) -> None:
    """Applique le thème à l'application (feuille de style + palette Qt native)."""
    native = app.palette()
    native.setColor(QPalette.ColorRole.Window, palette.qcolor("window"))
    native.setColor(QPalette.ColorRole.Base, palette.qcolor("surface"))
    native.setColor(QPalette.ColorRole.AlternateBase, palette.qcolor("surface_alt"))
    native.setColor(QPalette.ColorRole.WindowText, palette.qcolor("text"))
    native.setColor(QPalette.ColorRole.Text, palette.qcolor("text"))
    native.setColor(QPalette.ColorRole.Button, palette.qcolor("surface"))
    native.setColor(QPalette.ColorRole.ButtonText, palette.qcolor("text"))
    native.setColor(QPalette.ColorRole.Highlight, palette.qcolor("accent"))
    native.setColor(QPalette.ColorRole.HighlightedText, palette.qcolor("on_accent"))
    native.setColor(QPalette.ColorRole.ToolTipBase, palette.qcolor("surface_alt"))
    native.setColor(QPalette.ColorRole.ToolTipText, palette.qcolor("text"))
    native.setColor(QPalette.ColorRole.PlaceholderText, palette.qcolor("text_muted"))
    app.setPalette(native)
    app.setStyleSheet(build_stylesheet(palette))


def palette_for(mode: str, accent: str = "#ff2e63") -> Palette:
    """Renvoie la palette demandée (``dark``, ``light`` ou ``system``)."""
    if mode == "light":
        return light_palette(accent)
    if mode == "system":
        scheme = QApplication.styleHints().colorScheme()
        from PySide6.QtCore import Qt

        if scheme == Qt.ColorScheme.Light:
            return light_palette(accent)
        return dark_palette(accent)
    return dark_palette(accent)
