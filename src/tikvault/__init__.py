"""TikVault — téléchargement massif de vidéos TikTok.

Architecture hexagonale :

* :mod:`tikvault.domain`         — modèles et règles métier (sans dépendance externe)
* :mod:`tikvault.services`       — orchestration (file d'attente, téléchargement, veille)
* :mod:`tikvault.infrastructure` — adaptateurs (yt-dlp, SQLite, proxies, cookies)
* :mod:`tikvault.ui`             — interface PySide6, découplée du moteur par des signaux
"""

from __future__ import annotations

__version__ = "2.0.0"
__app_name__ = "TikVault"
__all__ = ["__app_name__", "__version__"]
