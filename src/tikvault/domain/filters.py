"""Règles métier de filtrage des vidéos.

Appliquées *après* résolution des métadonnées et *avant* téléchargement :
une vidéo filtrée ne consomme aucune bande passante.
"""

from __future__ import annotations

from dataclasses import dataclass

from tikvault.domain.models import FilterOptions, VideoMeta


@dataclass(slots=True, frozen=True)
class FilterDecision:
    """Verdict du filtre : accepté, ou refusé avec une raison lisible."""

    accepted: bool
    reason: str = ""

    @classmethod
    def accept(cls) -> FilterDecision:
        return cls(True)

    @classmethod
    def reject(cls, reason: str) -> FilterDecision:
        return cls(False, reason)


class VideoFilter:
    """Applique une :class:`FilterOptions` à des métadonnées vidéo.

    Les critères sans information disponible (ex. durée inconnue) **ne bloquent pas**
    le téléchargement : mieux vaut un faux positif qu'une vidéo perdue.
    """

    def __init__(self, options: FilterOptions) -> None:
        self._options = options

    @property
    def options(self) -> FilterOptions:
        return self._options

    def evaluate(self, meta: VideoMeta) -> FilterDecision:
        opts = self._options

        if opts.date_from and meta.upload_date and meta.upload_date < opts.date_from:
            return FilterDecision.reject(f"publiée avant le {opts.date_from.isoformat()}")

        if opts.date_to and meta.upload_date and meta.upload_date > opts.date_to:
            return FilterDecision.reject(f"publiée après le {opts.date_to.isoformat()}")

        if (
            opts.min_duration is not None
            and meta.duration is not None
            and meta.duration < opts.min_duration
        ):
            return FilterDecision.reject(f"durée {meta.duration:.0f}s < {opts.min_duration:.0f}s")

        if (
            opts.max_duration is not None
            and meta.duration is not None
            and meta.duration > opts.max_duration
        ):
            return FilterDecision.reject(f"durée {meta.duration:.0f}s > {opts.max_duration:.0f}s")

        if (
            opts.min_views is not None
            and meta.view_count is not None
            and meta.view_count < opts.min_views
        ):
            return FilterDecision.reject(f"{meta.view_count} vues < {opts.min_views}")

        return FilterDecision.accept()


class QuotaTracker:
    """Compteur partagé pour respecter ``max_items`` au sein d'une même source."""

    def __init__(self, max_items: int | None) -> None:
        self.max_items = max_items
        self.consumed = 0

    @property
    def exhausted(self) -> bool:
        return self.max_items is not None and self.consumed >= self.max_items

    def remaining(self) -> int | None:
        if self.max_items is None:
            return None
        return max(0, self.max_items - self.consumed)

    def consume(self, count: int = 1) -> None:
        self.consumed += count
