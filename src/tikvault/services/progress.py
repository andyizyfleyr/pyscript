"""Relais de progression : relie les callbacks d'extracteur (threads) au bus asyncio.

yt-dlp rappelle sa *progress hook* depuis le thread de téléchargement. Il est donc
interdit d'y toucher directement à la boucle asyncio : on passe par
``run_coroutine_threadsafe``. On en profite pour **throttler** (l'UI n'a aucun intérêt
à recevoir 200 événements par seconde).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from tikvault.domain.events import EventBus, TaskProgress
from tikvault.domain.ports import DownloadProgress


class ProgressRelay:
    """Callback de progression thread-safe, avec limitation de débit."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        bus: EventBus,
        task_id: str,
        *,
        min_interval: float = 0.2,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._loop = loop
        self._bus = bus
        self._task_id = task_id
        self._min_interval = min_interval
        self._clock = clock or time.monotonic
        self._last_emit = 0.0
        self._last_ratio = -1.0
        self._closed = False

    def __call__(self, progress: DownloadProgress) -> None:
        """Appelé depuis n'importe quel thread. Ne bloque jamais."""
        if self._closed:
            return

        now = self._clock()
        ratio = progress.ratio
        # On émet si le délai minimal est écoulé, ou si un jalon de 1 % a été franchi,
        # ou si l'on atteint une phase différente (post-traitement, fin…).
        if (
            now - self._last_emit < self._min_interval
            and abs(ratio - self._last_ratio) < 0.01
            and progress.phase == "téléchargement"
        ):
            return

        self._last_emit = now
        self._last_ratio = ratio
        event = TaskProgress(
            task_id=self._task_id,
            progress=ratio,
            bytes_done=progress.bytes_done,
            bytes_total=progress.bytes_total,
            speed_bps=progress.speed_bps,
            eta_seconds=progress.eta_seconds,
            phase=progress.phase,
        )
        try:
            asyncio.run_coroutine_threadsafe(self._bus.publish(event), self._loop)
        except RuntimeError:
            # Boucle déjà fermée (arrêt de l'application) : on ignore silencieusement.
            self._closed = True

    def close(self) -> None:
        self._closed = True
