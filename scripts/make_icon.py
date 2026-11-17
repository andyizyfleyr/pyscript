#!/usr/bin/env python3
"""Génère les icônes de l'application (``assets/icon.png`` et ``assets/icon.ico``).

Le script est volontairement **sans dépendance** (ni Pillow, ni Qt) : il dessine
l'icône pixel par pixel puis encode un PNG à la main (``zlib`` + ``struct``), ce qui
permet de régénérer les binaires sur n'importe quelle machine, y compris dans une
image minimale de compilation.

Le tracé reprend l'identité de l'interface : dégradé rose/violet de la marque,
flèche de téléchargement blanche et barre de destination.

Usage
-----
.. code-block:: console

    python scripts/make_icon.py            # régénère les deux fichiers
    python scripts/make_icon.py --size 512 # autre taille de PNG
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

# Couleurs de la marque (identiques au thème sombre de l'interface).
HAUT = (255, 46, 99)  # #ff2e63
BAS = (124, 58, 237)  # #7c3aed
BLANC = (255, 255, 255)

#: Sur-échantillonnage anti-crénelage (chaque pixel = 4x4 sous-pixels).
SS = 4


def _mix(haut: tuple[int, int, int], bas: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """Interpolation verticale entre les deux couleurs du dégradé (``t`` entre 0 et 1)."""
    return (
        round(haut[0] + (bas[0] - haut[0]) * t),
        round(haut[1] + (bas[1] - haut[1]) * t),
        round(haut[2] + (bas[2] - haut[2]) * t),
    )


def _dans_carre_arrondi(x: float, y: float, cote: float, rayon: float) -> float:
    """Renvoie 1.0 si le point est dans le carré arrondi, 0.0 sinon (bords nets)."""
    if x < 0 or y < 0 or x > cote or y > cote:
        return 0.0
    # Coins : on teste la distance au centre du cercle de rayon.
    cx = rayon if x < rayon else (cote - rayon if x > cote - rayon else None)
    cy = rayon if y < rayon else (cote - rayon if y > cote - rayon else None)
    if cx is None or cy is None:
        return 1.0
    return 1.0 if (x - cx) ** 2 + (y - cy) ** 2 <= rayon**2 else 0.0


def _dans_fleche(x: float, y: float) -> bool:
    """Flèche de téléchargement : tige verticale puis pointe, en coordonnées 0..256."""
    if 112 <= x <= 144 and 56 <= y <= 148:  # tige
        return True
    if 148 <= y <= 192:  # pointe (triangle)
        largeur = (192 - y) / 44 * 52  # 52 px de demi-largeur au sommet du triangle
        return abs(x - 128) <= largeur
    return False


def _dans_barre(x: float, y: float) -> bool:
    """Barre de destination sous la flèche."""
    return 204 <= y <= 220 and 72 <= x <= 184


def _couleur(x: float, y: float) -> tuple[int, int, int, int] | None:
    """Couleur RGBA d'un point, ou ``None`` s'il est transparent."""
    if not _dans_carre_arrondi(x, y, 256, 56):
        return None
    if _dans_fleche(x, y) or _dans_barre(x, y):
        return (*BLANC, 255)
    return (*_mix(HAUT, BAS, y / 256), 255)


def dessiner(taille: int) -> bytes:
    """Rasterise l'icône en ``taille`` x ``taille`` pixels (lignes RGBA brutes)."""
    echelle = taille / 256
    pixels = bytearray()
    for ligne in range(taille):
        for colonne in range(taille):
            # Moyenne des SSxSS sous-pixels centrés sur le pixel courant.
            r = v = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    px = (colonne + (sx + 0.5) / SS) / echelle
                    py = (ligne + (sy + 0.5) / SS) / echelle
                    couleur = _couleur(px, py)
                    if couleur is not None:
                        r += couleur[0]
                        v += couleur[1]
                        b += couleur[2]
                        a += couleur[3]
            total = SS * SS
            pixels += bytes((r // total, v // total, b // total, a // total))
    return bytes(pixels)


def encoder_png(taille: int, donnees: bytes) -> bytes:
    """Encode un PNG RGBA 8 bits à partir de données brutes (sans filtre de ligne)."""

    def bloc(etype: bytes, contenu: bytes) -> bytes:
        return (
            struct.pack(">I", len(contenu))
            + etype
            + contenu
            + struct.pack(">I", zlib.crc32(etype + contenu) & 0xFFFFFFFF)
        )

    brut = b"".join(b"\x00" + donnees[i * taille * 4 : (i + 1) * taille * 4] for i in range(taille))
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloc(b"IHDR", struct.pack(">IIBBBBB", taille, taille, 8, 6, 0, 0, 0))
        + bloc(b"IDAT", zlib.compress(brut, 9))
        + bloc(b"IEND", b"")
    )


def encoder_ico(png: bytes, taille: int) -> bytes:
    """Encapsule un PNG dans un conteneur ICO (format accepté depuis Windows Vista)."""
    largeur = 0 if taille >= 256 else taille  # 0 signifie « 256 »
    entree = struct.pack("<BBBBHHII", largeur, largeur, 0, 0, 1, 32, len(png), 22)
    return struct.pack("<HHH", 0, 1, 1) + entree + png


def main() -> None:
    parser = argparse.ArgumentParser(description="Génère les icônes de TikVault.")
    parser.add_argument("--size", type=int, default=256, help="Taille du PNG (défaut : 256)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets",
        help="Dossier de sortie",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    donnees = dessiner(args.size)
    png = encoder_png(args.size, donnees)
    (args.out / "icon.png").write_bytes(png)
    (args.out / "icon.ico").write_bytes(encoder_ico(png, args.size))
    print(f"icônes écrites dans {args.out} ({len(png)} octets)")


if __name__ == "__main__":
    main()
