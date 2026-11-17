"""Ressources embarquées dans l'exécutable PyInstaller — FFmpeg notamment.

Quand TikVault est distribué en ``.exe``, un dossier ``ffmpeg/`` peut être glissé
dans l'archive interne (voir ``build/tikvault.spec``). Au démarrage, on le place
en tête du ``PATH`` : ``shutil.which("ffmpeg")`` et yt-dlp le trouvent alors
naturellement, sans que l'utilisateur ait quoi que ce soit à installer.

Si un FFmpeg système existe déjà, le FFmpeg embarqué reste prioritaire : la
version livrée avec l'application est celle qui a été testée avec elle.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen_dir() -> Path | None:
    """Dossier d'extraction de PyInstaller (``sys._MEIPASS``), ou ``None``."""
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def bundled_ffmpeg_dir() -> Path | None:
    """Dossier du FFmpeg embarqué, ou ``None`` s'il n'y en a pas."""
    base = frozen_dir()
    if base is None:
        return None
    dossier = base / "ffmpeg"
    executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    return dossier if (dossier / executable).exists() else None


def ensure_ffmpeg_on_path() -> bool:
    """Place le FFmpeg embarqué en tête du ``PATH``. Renvoie ``True`` si fait."""
    dossier = bundled_ffmpeg_dir()
    if dossier is None:
        return False
    os.environ["PATH"] = str(dossier) + os.pathsep + os.environ.get("PATH", "")
    return True


__all__ = ["bundled_ffmpeg_dir", "ensure_ffmpeg_on_path", "frozen_dir"]
