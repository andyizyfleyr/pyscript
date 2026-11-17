"""Registre d'extracteurs + chaîne de secours (architecture « plugins »).

Ajouter un moteur de téléchargement se résume à :

.. code-block:: python

    class MonMoteur(VideoExtractor):
        name = "mon-moteur"
        priority = 50          # plus c'est haut, plus c'est essayé tôt
        ...

    registry.register(MonMoteur())

La :class:`ChainExtractor` essaye chaque moteur dans l'ordre et bascule
automatiquement sur le suivant en cas d'échec — c'est le « plan B » demandé :
quand TikTok casse yt-dlp, l'extracteur web prend le relais.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from tikvault.domain.errors import ExtractionError, classify_error
from tikvault.domain.models import DownloadOptions, SourceType, VideoMeta
from tikvault.domain.ports import (
    DownloadProgress,
    DownloadResult,
    ExtractionContext,
    ProgressCallback,
    ResolvedSource,
    VideoExtractor,
)

logger = structlog.get_logger(__name__)


class ExtractorRegistry:
    """Contient les moteurs disponibles, triés par priorité décroissante."""

    def __init__(self) -> None:
        self._extractors: list[VideoExtractor] = []

    def register(self, extractor: VideoExtractor) -> VideoExtractor:
        self._extractors.append(extractor)
        self._extractors.sort(key=lambda e: e.priority, reverse=True)
        logger.debug("extracteur_enregistre", nom=extractor.name, priorite=extractor.priority)
        return extractor

    @property
    def extractors(self) -> list[VideoExtractor]:
        return list(self._extractors)

    def names(self) -> list[str]:
        return [extractor.name for extractor in self._extractors]

    def chain(self) -> ChainExtractor:
        """Renvoie un extracteur composite utilisable comme un moteur unique."""
        return ChainExtractor(self._extractors)


class ChainExtractor(VideoExtractor):
    """Essaie les moteurs enregistrés dans l'ordre, jusqu'au premier succès."""

    name = "chaine"
    priority = 0

    def __init__(self, extractors: list[VideoExtractor] | None = None) -> None:
        self._extractors = list(extractors or [])
        #: Nom du dernier moteur ayant réussi (affiché dans l'historique).
        self.last_used: str | None = None

    def supports(self, url: str) -> bool:
        return any(extractor.supports(url) for extractor in self._extractors)

    async def probe(self, url: str, context: ExtractionContext) -> VideoMeta:
        errors: list[str] = []
        for extractor in self._candidates(url):
            try:
                meta = await extractor.probe(url, context)
            except Exception as exc:
                errors.append(f"{extractor.name}: {classify_error(exc).user_message}")
                continue
            self.last_used = extractor.name
            return meta
        raise ExtractionError(" ; ".join(errors) or "Aucun extracteur disponible.")

    async def resolve_source(
        self,
        source: str,
        source_type: SourceType,
        context: ExtractionContext,
        limit: int | None = None,
    ) -> ResolvedSource:
        errors: list[str] = []
        for extractor in self._extractors:
            try:
                resolved = await extractor.resolve_source(source, source_type, context, limit=limit)
            except Exception as exc:
                errors.append(f"{extractor.name}: {classify_error(exc).user_message}")
                continue
            if not resolved.urls:
                errors.append(f"{extractor.name}: aucune vidéo trouvée")
                continue
            self.last_used = extractor.name
            return resolved
        raise ExtractionError(" ; ".join(errors) or "Aucun extracteur disponible.")

    async def download(
        self,
        url: str,
        destination: Path,
        options: DownloadOptions,
        context: ExtractionContext,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        target = destination
        errors: list[str] = []
        for index, extractor in enumerate(self._candidates(url)):
            try:
                result = await extractor.download(url, target, options, context, progress=progress)
            except Exception as exc:
                errors.append(f"{extractor.name}: {classify_error(exc).user_message}")
                logger.warning(
                    "extracteur_en_echec",
                    moteur=extractor.name,
                    suivant=(
                        self._candidates(url)[index + 1].name
                        if index + 1 < len(self._candidates(url))
                        else None
                    ),
                    erreur=str(exc)[:200],
                )
                continue
            self.last_used = extractor.name
            return result
        raise ExtractionError(" ; ".join(errors) or "Aucun extracteur disponible.")

    def _candidates(self, url: str) -> list[VideoExtractor]:
        matching = [e for e in self._extractors if e.supports(url)]
        return matching or list(self._extractors)


__all__ = ["ChainExtractor", "DownloadProgress", "ExtractorRegistry"]
