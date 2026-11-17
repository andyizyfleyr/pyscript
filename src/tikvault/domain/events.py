"""Événements émis par le moteur + bus de publication/souscription asynchrone.

Le moteur **ne connaît pas Qt** : il publie des événements simples sur le bus.
L'UI s'abonne et les convertit en signaux Qt (voir :mod:`tikvault.ui.engine_bridge`).
C'est ce découplage qui permet de piloter le même moteur en ligne de commande.
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from tikvault.domain.models import BatchStats, DownloadTask, TaskState, VideoMeta, utc_now

# Un abonné peut être synchrone ou asynchrone.
Subscriber = Callable[["EngineEvent"], Any]


@dataclass(slots=True, kw_only=True)
class EngineEvent:
    """Événement de base : horodaté, sérialisable, sans référence à Qt.

    ``at`` est déclaré « mot-clé uniquement » : sans cela, un champ par défaut en base
    interdirait tout champ obligatoire dans les sous-classes.
    """

    at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class TaskAdded(EngineEvent):
    task: DownloadTask


@dataclass(slots=True)
class TaskStateChanged(EngineEvent):
    task_id: str
    previous: TaskState
    current: TaskState
    message: str | None = None


@dataclass(slots=True)
class TaskProgress(EngineEvent):
    task_id: str
    progress: float
    bytes_done: int
    bytes_total: int
    speed_bps: float
    eta_seconds: float | None
    phase: str = "téléchargement"


@dataclass(slots=True)
class TaskMetaResolved(EngineEvent):
    task_id: str
    meta: VideoMeta


@dataclass(slots=True)
class TaskCompleted(EngineEvent):
    task: DownloadTask


@dataclass(slots=True)
class TaskFailed(EngineEvent):
    task_id: str
    kind: str
    message: str
    will_retry: bool
    attempt: int


@dataclass(slots=True)
class TaskRetrying(EngineEvent):
    task_id: str
    attempt: int
    delay_seconds: float
    reason: str


@dataclass(slots=True)
class StatsUpdated(EngineEvent):
    stats: BatchStats


@dataclass(slots=True)
class BatchFinished(EngineEvent):
    done: int
    failed: int
    skipped: int
    bytes_downloaded: int
    elapsed_seconds: float


@dataclass(slots=True)
class SourceResolved(EngineEvent):
    """Une source (profil, hashtag…) a été développée en N tâches."""

    source: str
    label: str
    count: int


@dataclass(slots=True)
class WatcherEvent(EngineEvent):
    account: str
    new_videos: int
    message: str


@dataclass(slots=True)
class LogEvent(EngineEvent):
    level: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UpdateAvailable(EngineEvent):
    current_version: str
    latest_version: str
    component: str = "yt-dlp"


class EventBus:
    """Bus d'événements minimaliste, thread-safe et typé.

    Les abonnés sont appelés dans l'ordre d'inscription. Une exception levée par un
    abonné est isolée : elle ne casse jamais le moteur.
    """

    def __init__(self) -> None:
        self._subscribers: dict[type[EngineEvent], list[Subscriber]] = defaultdict(list)

    def subscribe(self, event_type: type[EngineEvent], callback: Subscriber) -> Callable[[], None]:
        """Abonne ``callback`` ; renvoie une fonction de désabonnement."""
        self._subscribers[event_type].append(callback)

        def unsubscribe() -> None:
            subs = self._subscribers.get(event_type)
            if subs and callback in subs:
                subs.remove(callback)

        return unsubscribe

    def subscribe_all(self, callback: Subscriber) -> Callable[[], None]:
        """Abonne ``callback`` à **tous** les événements (utile pour l'UI et les logs)."""
        return self.subscribe(EngineEvent, callback)

    async def publish(self, event: EngineEvent) -> None:
        """Diffuse un événement à tous les abonnés concernés (type exact + type de base)."""
        targets: list[Subscriber] = [*self._subscribers.get(EngineEvent, [])]
        for event_type, subs in self._subscribers.items():
            if event_type is not EngineEvent and isinstance(event, event_type):
                targets.extend(subs)

        for callback in dict.fromkeys(targets):  # préserve l'ordre, supprime les doublons
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # un abonné défaillant ne doit jamais casser le moteur
                structlog.get_logger(__name__).exception(
                    "abonne_defaillant", event=event.__class__.__name__
                )

    def clear(self) -> None:
        self._subscribers.clear()
