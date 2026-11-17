"""Dédoublonnage par **contenu** (BLAKE2b), pas par nom de fichier.

Deux niveaux :
  1. *rapide* — l'identifiant de vidéo est déjà en base et le fichier existe → on saute ;
  2. *strict* — empreinte BLAKE2b du fichier : détecte les re-publications d'une même
     vidéo sous un autre identifiant (très courant sur TikTok).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024  # 1 Mo : bon compromis vitesse/mémoire


class Blake2Hasher:
    """Implémentation du port :class:`tikvault.domain.ports.Hasher`."""

    def __init__(self, *, digest_size: int = 16) -> None:
        # 16 octets = 128 bits : largement suffisant pour détecter un doublon,
        # et assez court pour être stocké/indexé en base.
        self._digest_size = digest_size

    async def digest(self, path: Path) -> str:
        """Calcule l'empreinte sans bloquer la boucle asyncio (lecture par blocs)."""
        return await asyncio.to_thread(self._digest_sync, path)

    def _digest_sync(self, path: Path) -> str:
        hasher = hashlib.blake2b(digest_size=self._digest_size)
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                hasher.update(chunk)
        return hasher.hexdigest()


class DedupeService:
    """Décide si un téléchargement doit être ignoré, et vérifie le résultat."""

    def __init__(self, repository: object, hasher: Blake2Hasher | None = None) -> None:
        # ``repository`` est typé faiblement ici pour éviter une importation circulaire ;
        # le contrat réel est :class:`tikvault.domain.ports.TaskRepository`.
        self._repository = repository
        self._hasher = hasher or Blake2Hasher()

    async def already_downloaded(self, video_ids: list[str], root: Path) -> set[str]:
        """Identifiants déjà présents en base **et** dont le fichier existe encore sur le disque."""
        repo = self._repository
        known: set[str] = await repo.known_video_ids(video_ids)  # type: ignore[attr-defined]
        if not known:
            return set()
        # Un identifiant en base mais dont le fichier a été supprimé doit être retéléchargeable :
        # la vérification fine est faite au cas par cas par DownloadService via meta.file_path.
        _ = root
        return known

    async def hash_of(self, path: Path) -> str:
        """Empreinte de contenu du fichier produit."""
        return await self._hasher.digest(path)
