"""Ports (interfaces) de l'architecture hexagonale.

Les services ne dépendent que de ces contrats. Les implémentations concrètes
(yt-dlp, SQLite, pool de proxies, notifications Qt) vivent dans
:mod:`tikvault.infrastructure` et sont injectées au démarrage
(voir :mod:`tikvault.app`).
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tikvault.domain.models import (
    DownloadOptions,
    DownloadTask,
    FailureKind,
    SourceType,
    TaskState,
    VideoMeta,
    WatchedAccount,
)

# Callback de progression appelé par les extracteurs (thread-safe imposé par l'implémentation).
ProgressCallback = Callable[["DownloadProgress"], None]


@dataclass(slots=True)
class DownloadProgress:
    """Instantané de progression d'un téléchargement."""

    bytes_done: int = 0
    bytes_total: int = 0
    speed_bps: float = 0.0
    eta_seconds: float | None = None
    phase: str = "téléchargement"

    @property
    def ratio(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.bytes_done / self.bytes_total))


@dataclass(slots=True)
class ExtractionContext:
    """Paramètres réseau/identité passés à un extracteur pour une tentative donnée."""

    proxy: str | None = None
    user_agent: str | None = None
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None  # ex. "chrome", "firefox:profile"
    timeout: float = 30.0
    cancel: Any = None  # asyncio.Event (non typé pour éviter l'import asyncio ici)


@dataclass(slots=True)
class DownloadResult:
    """Résultat concret d'un téléchargement réussi."""

    file_path: Path
    size: int
    extractor: str
    meta: VideoMeta | None = None
    resumed: bool = False
    postprocessed: bool = False


@dataclass(slots=True)
class ResolvedSource:
    """Une source d'entrée développée en tâches concrètes."""

    source_type: SourceType
    label: str
    original: str
    urls: list[str] = field(default_factory=list)
    metas: list[VideoMeta] = field(default_factory=list)


class VideoExtractor(abc.ABC):
    """Contrat d'un moteur de téléchargement (yt-dlp, API web, futur moteur…).

    Pour ajouter un moteur : sous-classer, déclarer un ``priority`` plus élevé
    (essayé en premier) et l'enregistrer dans
    :class:`tikvault.infrastructure.extractors.registry.ExtractorRegistry`.
    """

    name: str = "abstract"
    priority: int = 0

    @abc.abstractmethod
    def supports(self, url: str) -> bool:
        """L'extracteur sait-il traiter cette URL ?"""

    @abc.abstractmethod
    async def probe(self, url: str, context: ExtractionContext) -> VideoMeta:
        """Récupère uniquement les métadonnées (léger, sert aux filtres et miniatures)."""

    @abc.abstractmethod
    async def resolve_source(
        self,
        source: str,
        source_type: SourceType,
        context: ExtractionContext,
        limit: int | None = None,
    ) -> ResolvedSource:
        """Développe un profil / hashtag / son / playlist en liste de vidéos."""

    @abc.abstractmethod
    async def download(
        self,
        url: str,
        destination: Path,
        options: DownloadOptions,
        context: ExtractionContext,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Télécharge la vidéo vers ``destination`` (sans extension imposée)."""

    def close(self) -> None:
        """Libère les ressources éventuelles (noop par défaut)."""
        return None


@runtime_checkable
class TaskRepository(Protocol):
    """Persistance des tâches, de l'historique et des comptes surveillés."""

    async def init(self) -> None: ...

    async def close(self) -> None: ...

    # --- Tâches -----------------------------------------------------------
    async def add_tasks(self, tasks: Sequence[DownloadTask]) -> None: ...

    async def get_task(self, task_id: str) -> DownloadTask | None: ...

    async def save_task(self, task: DownloadTask) -> None: ...

    async def list_tasks(
        self,
        *,
        states: Iterable[TaskState] | None = None,
        query: str | None = None,
        failure_kind: FailureKind | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[DownloadTask]: ...

    async def count_tasks(
        self,
        *,
        states: Iterable[TaskState] | None = None,
        query: str | None = None,
        failure_kind: FailureKind | None = None,
    ) -> int: ...

    async def set_positions(self, ordered_ids: Sequence[str]) -> None: ...

    async def delete_task(self, task_id: str) -> None: ...

    async def recover_interrupted(self) -> int:
        """Remet en file les tâches interrompues par un crash/fermeture."""
        ...

    async def aggregate_stats(self) -> dict[str, int]: ...

    # --- Dédoublonnage ----------------------------------------------------
    async def known_video_ids(self, ids: Iterable[str]) -> set[str]: ...

    async def find_by_hash(self, file_hash: str) -> DownloadTask | None: ...

    # --- Comptes surveillés ----------------------------------------------
    async def list_watched(self, *, enabled_only: bool = False) -> list[WatchedAccount]: ...

    async def upsert_watched(self, account: WatchedAccount) -> None: ...

    async def delete_watched(self, username: str) -> None: ...


class ProxyProvider(Protocol):
    """Fournit un proxy à chaque tentative et signale les échecs."""

    def next(self) -> str | None: ...

    def report_failure(self, proxy: str) -> None: ...

    def report_success(self, proxy: str) -> None: ...

    def __len__(self) -> int: ...


class UserAgentProvider(Protocol):
    def next(self) -> str: ...


class CookieProvider(abc.ABC):
    """Fournit un fichier de cookies Netscape exploitable par yt-dlp."""

    @abc.abstractmethod
    async def export(self) -> Path | None:
        """Renvoie le chemin du fichier de cookies, ou ``None`` si indisponible."""

    @abc.abstractmethod
    def browser_spec(self) -> str | None:
        """Spécification ``navigateur[:profil][:keyring][:container]`` pour yt-dlp."""


class Notifier(Protocol):
    """Notifications système (fin de lot, échec critique…)."""

    def notify(self, title: str, message: str, *, level: str = "info") -> None: ...


class Updater(Protocol):
    """Mise à jour automatique du moteur (yt-dlp évolue très souvent)."""

    async def current_version(self) -> str: ...

    async def check(self) -> str | None:
        """Renvoie la dernière version disponible, ou ``None`` si à jour/injoignable."""

    async def apply(self) -> tuple[bool, str]:
        """Applique la mise à jour ; renvoie (succès, message)."""


@runtime_checkable
class Hasher(Protocol):
    """Calcul de l'empreinte d'un fichier (dédoublonnage par contenu)."""

    async def digest(self, path: Path) -> str: ...


class AsyncClosable(Protocol):
    async def aclose(self) -> None: ...


__all__ = [
    "AsyncClosable",
    "AsyncIterator",
    "Awaitable",
    "CookieProvider",
    "DownloadProgress",
    "DownloadResult",
    "ExtractionContext",
    "Hasher",
    "Notifier",
    "ProgressCallback",
    "ProxyProvider",
    "ResolvedSource",
    "TaskRepository",
    "Updater",
    "UserAgentProvider",
    "VideoExtractor",
]
