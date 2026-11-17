"""Icônes vectorielles : rendu SVG à la volée, teinte paramétrable, cache mémoire.

Aucun fichier image n'est embarqué : les tracés sont définis ici (style « feather »),
puis rastérisés dans un ``QPixmap`` haute résolution. Changer de thème recolore donc
toutes les icônes sans recharger quoi que ce soit.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Tracés SVG (viewBox 24x24, traits de 2 px, bouts arrondis).
_PATHS: dict[str, str] = {
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/>'
    '<rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "queue": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>'
    '<line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1.2"/><circle cx="4" cy="12" r="1.2"/>'
    '<circle cx="4" cy="18" r="1.2"/>',
    "history": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
    "settings": '<circle cx="12" cy="12" r="3.2"/>'
    '<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 8.9 19a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 8.4a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H22a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "play": '<polygon points="6 4 20 12 6 20 6 4"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "download": '<path d="M12 3v12"/><polyline points="7 11 12 16 17 11"/><path d="M4 20h16"/>',
    "refresh": '<polyline points="21 4 21 10 15 10"/><polyline points="3 20 3 14 9 14"/>'
    '<path d="M20.5 14a8.5 8.5 0 0 1-16 3M3.5 10a8.5 8.5 0 0 1 16-3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/>',
    "export": '<path d="M12 15V3"/><polyline points="8 7 12 3 16 7"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "trash": '<polyline points="3 6 21 6"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.5.5l2-2A5 5 0 0 0 12.5 4.5l-1 1"/>'
    '<path d="M14 11a5 5 0 0 0-7.5-.5l-2 2A5 5 0 0 0 11.5 19.5l1-1"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
    '<polyline points="14 3 14 8 19 8"/>',
    "bell": '<path d="M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    "check": '<polyline points="4 12.5 9.5 18 20 6.5"/>',
    "alert": '<path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'
    '<line x1="12" y1="9" x2="12" y2="13.5"/><circle cx="12" cy="17" r="0.6"/>',
    "eye": '<path d="M1.5 12S5 5.5 12 5.5 22.5 12 22.5 12 19 18.5 12 18.5 1.5 12 1.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"/>',
    "hash": '<line x1="5" y1="9" x2="19" y2="9"/><line x1="5" y1="15" x2="19" y2="15"/>'
    '<line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "logo": '<path d="M14 3v10.5a3.5 3.5 0 1 1-3-3.46"/><path d="M14 3c.5 2.5 2 4 4.5 4.3"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><polyline points="12 7.5 12 12 15 13.5"/>',
    "gauge": '<path d="M4 18a9 9 0 1 1 16 0"/><line x1="12" y1="18" x2="15.5" y2="10.5"/>',
    "disk": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/>'
    '<path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
}

# Icônes « pleines » (remplies plutôt que tracées).
_FILLED = {"play", "pause", "stop", "logo"}


@lru_cache(maxsize=256)
def _render(name: str, color: str, size: int) -> QPixmap:
    """Rastérise une icône dans un pixmap haute résolution (net sur écrans HiDPI)."""
    if name not in _PATHS:
        name = "file"
    stroke = "none" if name in _FILLED else color
    fill = color if name in _FILLED else "none"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>'
    )

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    return pixmap


def icon(name: str, color: str = "#e8eaf0", size: int = 40) -> QIcon:
    """Icône mise en cache, teinte à la demande."""
    return QIcon(_render(name, color, size))


def pixmap(name: str, color: str = "#e8eaf0", size: int = 40) -> QPixmap:
    """Pixmap brut (utile pour les en-têtes de tableau ou les boutons)."""
    return _render(name, color, size)


def icon_size(base: int = 18) -> QSize:
    return QSize(base, base)


def available_icons() -> tuple[str, ...]:
    return tuple(sorted(_PATHS))
