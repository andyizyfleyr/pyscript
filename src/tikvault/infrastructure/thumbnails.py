"""Cache de miniatures sur disque, alimenté en asynchrone.

L'UI demande une miniature, on renvoie immédiatement le chemin du cache :
si l'image n'est pas encore téléchargée, elle le sera en arrière-plan et un rappel
préviendra l'interface. Aucun accès réseau depuis le thread graphique.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger(__name__)

ReadyCallback = Callable[[str, Path], Awaitable[None] | None]


class ThumbnailCache:
    """Télécharge et met en cache les vignettes (une requête par URL, jamais deux)."""

    def __init__(self, cache_dir: Path, *, client: httpx.AsyncClient | None = None) -> None:
        self._dir = cache_dir.expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._inflight: dict[str, asyncio.Task[Path | None]] = {}

    def path_for(self, url: str) -> Path:
        """Chemin de cache déterministe pour une URL donnée."""
        digest = hashlib.blake2b(url.encode(), digest_size=12).hexdigest()
        return self._dir / f"{digest}.jpg"

    def get(self, url: str) -> Path | None:
        """Renvoie le chemin si la miniature est déjà en cache."""
        path = self.path_for(url)
        return path if path.exists() and path.stat().st_size > 0 else None

    async def fetch(self, url: str) -> Path | None:
        """Télécharge (si nécessaire) et renvoie le chemin local."""
        cached = self.get(url)
        if cached is not None:
            return cached

        existing = self._inflight.get(url)
        if existing is not None:
            return await existing

        task = asyncio.create_task(self._download(url))
        self._inflight[url] = task
        try:
            return await task
        finally:
            self._inflight.pop(url, None)

    def fetch_later(self, url: str, on_ready: ReadyCallback | None = None) -> Path | None:
        """Lance le téléchargement en arrière-plan ; renvoie le chemin déjà disponible."""
        cached = self.get(url)
        if cached is not None or url in self._inflight:
            return cached

        async def _run() -> None:
            path = await self._download(url)
            if path is not None and on_ready is not None:
                result = on_ready(url, path)
                if asyncio.iscoroutine(result):
                    await result

        self._inflight[url] = asyncio.create_task(_run())
        return cached

    async def _download(self, url: str) -> Path | None:
        target = self.path_for(url)
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        try:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code != 200 or not response.content:
                return None
            target.write_bytes(response.content)
        except (httpx.HTTPError, OSError) as exc:
            logger.debug("miniature_echec", url=url[:80], erreur=str(exc)[:120])
            return None
        finally:
            if owns:
                await client.aclose()
        return target

    async def aclose(self) -> None:
        for task in list(self._inflight.values()):
            task.cancel()
        if self._client is not None:
            await self._client.aclose()
