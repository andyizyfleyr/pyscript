"""Utilitaires disque : espace libre, taille d'un dossier, garde-fou avant téléchargement."""

from __future__ import annotations

import shutil
from pathlib import Path

from tikvault.domain.errors import DiskFullError

# On garde une marge de sécurité pour ne jamais saturer le disque système.
SAFETY_MARGIN_BYTES = 500 * 1024 * 1024  # 500 Mo


def free_bytes(path: Path) -> int:
    """Espace disponible sur le volume contenant ``path`` (le dossier est créé si besoin)."""
    target = path.expanduser()
    if not target.exists():
        target = target.parent if target.parent.exists() else Path.cwd()
    usage = shutil.disk_usage(target)
    return usage.free


def ensure_writable(path: Path, *, required: int = 50 * 1024 * 1024) -> Path:
    """Crée le dossier et vérifie qu'on peut y écrire ; lève :class:`DiskFullError` sinon."""
    target = path.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if free_bytes(target) < required + SAFETY_MARGIN_BYTES:
        msg = (
            f"Espace disque insuffisant sur {target} "
            f"(disponible : {free_bytes(target) / 1e9:.2f} Go, requis : {required / 1e9:.2f} Go)."
        )
        raise DiskFullError(msg)
    return target


def directory_size(path: Path) -> int:
    """Taille cumulée d'un dossier (0 s'il n'existe pas). Calcul synchrone, à lancer dans un thread."""
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue  # fichier supprimé pendant le parcours
    return total


def human_size(num_bytes: float) -> str:
    """Formatage lisible en français (« 1,4 Go » : virgule décimale)."""
    value = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(value) < 1024 or unit == "To":
            break
        value /= 1024
    return f"{value:.1f} {unit}".replace(".", ",")


def human_duration(seconds: float | None) -> str:
    """Formatage « 1 h 04 min 09 s » / « 12 s »."""
    if seconds is None:
        return "—"
    total = int(max(0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min {secs:02d} s"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"
