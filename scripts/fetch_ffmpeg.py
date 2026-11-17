#!/usr/bin/env python3
"""Télécharge FFmpeg (Windows 64 bits) dans ``build/ffmpeg/`` pour l'embarquer.

À lancer **une fois** avant de fabriquer l'exécutable :

.. code-block:: console

    uv run python scripts/fetch_ffmpeg.py

La recette PyInstaller (``build/tikvault.spec``) détecte ensuite le dossier et
glisse ``ffmpeg.exe`` + ``ffprobe.exe`` dans le ``.exe`` final ; au démarrage,
TikVault les place en tête du PATH (voir ``infrastructure/bundle.py``).

Script en bibliothèque standard uniquement (urllib + zipfile), sans dépendance.
Source : builds officiels BtbN (projet FFmpeg) publiés sur GitHub.

Note licence : ces builds sont sous GPL. TikVault (MIT) ne fait que les
distribuer côte à côte, ce que la GPL autorise (simple agrégation) ; les
sources de FFmpeg restent disponibles sur le dépôt BtbN/FFmpeg-Builds.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

#: Build statique Windows x64 (GPL, « latest » = suit les versions stables).
URL_DEFAUT = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)
#: Seuls ces exécutables sont extraits (ffplay est inutile à TikVault).
VOULUS = ("ffmpeg.exe", "ffprobe.exe")


def telecharger(url: str, destination: Path) -> None:
    """Téléchargement avec progression sommaire (le fichier fait ~170 Mo)."""

    def _progression(blocs: int, taille_bloc: int, total: int) -> None:
        if total > 0:
            pct = min(100, blocs * taille_bloc * 100 // total)
            sys.stdout.write(f"\r  téléchargement… {pct:3d} %")
            sys.stdout.flush()

    print(f"Source : {url}")
    urllib.request.urlretrieve(url, destination, reporthook=_progression)
    print("\r  téléchargement… 100 %")


def extraire(archive: Path, sortie: Path) -> list[str]:
    """Extrait uniquement ffmpeg.exe et ffprobe.exe, à plat dans ``sortie``."""
    sortie.mkdir(parents=True, exist_ok=True)
    extraits: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for membre in zf.namelist():
            nom = Path(membre).name
            if nom in VOULUS:
                with zf.open(membre) as source, (sortie / nom).open("wb") as cible:
                    shutil.copyfileobj(source, cible)
                extraits.append(nom)
    return extraits


def main() -> int:
    parser = argparse.ArgumentParser(description="Récupère FFmpeg pour l'embarquer dans le .exe.")
    parser.add_argument("--url", default=URL_DEFAUT, help="Archive zip FFmpeg à utiliser.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "build" / "ffmpeg",
        help="Dossier de sortie (défaut : build/ffmpeg).",
    )
    args = parser.parse_args()

    deja = [nom for nom in VOULUS if (args.out / nom).exists()]
    if len(deja) == len(VOULUS):
        print(f"FFmpeg déjà présent dans {args.out} — rien à faire.")
        print("(supprimez le dossier pour forcer un nouveau téléchargement)")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        telecharger(args.url, archive)
        extraits = extraire(archive, args.out)

    if sorted(extraits) != sorted(VOULUS):
        print(f"ERREUR : exécutables manquants dans l'archive (trouvés : {extraits})")
        return 1

    for nom in extraits:
        taille = (args.out / nom).stat().st_size / 1_048_576
        print(f"  {args.out / nom}  ({taille:.0f} Mo)")
    print("Terminé. La prochaine fabrication du .exe embarquera FFmpeg automatiquement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
