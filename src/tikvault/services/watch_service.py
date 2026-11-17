"""Veille de comptes : télécharge automatiquement les nouvelles vidéos d'un profil.

Fonctionnement : une boucle asyncio réveille chaque compte surveillé à son intervalle,
liste ses vidéos les plus récentes, compare aux identifiants déjà vus et met en file
uniquement les nouveautés. L'état (identifiants vus, dernier passage) est en base :
la veille survit à un redémarrage.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import timedelta

import structlog

from tikvault.domain import ports
from tikvault.domain.events import EventBus, LogEvent, WatcherEvent
from tikvault.domain.models import (
    DownloadOptions,
    DownloadTask,
    SourceType,
    WatchedAccount,
    utc_now,
)

logger = structlog.get_logger(__name__)

# Nombre de vidéos les plus récentes examinées à chaque passage.
PROBE_DEPTH = 40


class AccountWatcher:
    """Surveille des comptes TikTok et alimente la file de téléchargement."""

    def __init__(
        self,
        *,
        repository: ports.TaskRepository,
        extractor: ports.VideoExtractor,
        queue: object,
        bus: EventBus,
        tick_seconds: float = 30.0,
    ) -> None:
        # ``queue`` : contrat minimal attendu = ``await queue.enqueue_tasks(list[DownloadTask])``.
        self._repository = repository
        self._extractor = extractor
        self._queue = queue
        self._bus = bus
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._wakeup = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="tikvault-watcher")
        logger.info("veille_demarree", intervalle=self._tick_seconds)

    async def stop(self) -> None:
        self._running = False
        self._wakeup.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def wake_up(self) -> None:
        """Force un passage immédiat (bouton « vérifier maintenant »)."""
        self._wakeup.set()

    # ------------------------------------------------------------------
    async def add_account(self, account: WatchedAccount) -> None:
        await self._repository.upsert_watched(account)
        self.wake_up()

    async def remove_account(self, username: str) -> None:
        await self._repository.delete_watched(username.lstrip("@"))

    async def set_enabled(self, username: str, enabled: bool) -> None:
        accounts = await self._repository.list_watched()
        for account in accounts:
            if account.username == username.lstrip("@"):
                account.enabled = enabled
                await self._repository.upsert_watched(account)
                return

    # ------------------------------------------------------------------
    def _stop_requested(self) -> bool:
        """Arrêt demandé ? (lecture via une méthode pour éviter le figeage du typage)."""
        return not self._running

    async def _loop(self) -> None:
        while True:
            if self._stop_requested():
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._tick_seconds)
            self._wakeup.clear()
            if self._stop_requested():
                return
            try:
                await self.check_all()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("erreur_veille")

    async def check_all(self) -> int:
        """Vérifie tous les comptes dont l'intervalle est écoulé. Renvoie le total de nouveautés."""
        accounts = await self._repository.list_watched(enabled_only=True)
        now = utc_now()
        total = 0
        for account in accounts:
            due = (
                account.last_checked_at is None
                or account.last_checked_at + timedelta(minutes=account.interval_minutes) <= now
            )
            if not due:
                continue
            total += await self.check_account(account)
        return total

    async def check_account(self, account: WatchedAccount) -> int:
        """Un passage de veille sur un compte donné."""
        url = f"https://www.tiktok.com/@{account.username.lstrip('@')}"
        context = ports.ExtractionContext(timeout=account.options.socket_timeout)
        try:
            resolved = await self._extractor.resolve_source(
                url, SourceType.PROFILE, context, limit=PROBE_DEPTH
            )
        except Exception as exc:
            logger.warning("veille_echec", compte=account.username, erreur=str(exc)[:200])
            await self._bus.publish(
                LogEvent(level="warning", message=f"Veille de @{account.username} : échec ({exc})")
            )
            account.last_checked_at = utc_now()
            await self._repository.upsert_watched(account)
            return 0

        known = set(account.last_video_ids)
        fresh_ids = [meta.video_id for meta in resolved.metas if meta.video_id not in known]
        fresh = [
            (meta, resolved.urls[index])
            for index, meta in enumerate(resolved.metas)
            if meta.video_id in set(fresh_ids) and index < len(resolved.urls)
        ]

        tasks: list[DownloadTask] = []
        for index, (meta, video_url) in enumerate(fresh, start=1):
            tasks.append(
                DownloadTask(
                    url=video_url,
                    source_type=SourceType.PROFILE,
                    position=index,
                    meta=meta,
                    max_attempts=account.options.max_attempts,
                    options=account.options.model_copy(deep=True),
                )
            )

        # On mémorise tous les identifiants vus (borne à 2000 pour ne pas gonfler la base).
        account.last_video_ids = ([meta.video_id for meta in resolved.metas] + list(known))[:2000]
        account.last_checked_at = utc_now()
        await self._repository.upsert_watched(account)

        if tasks:
            await self._queue.enqueue_tasks(tasks)  # type: ignore[attr-defined]
            message = f"@{account.username} : {len(tasks)} nouvelle(s) vidéo(s) mise(s) en file."
            await self._bus.publish(
                WatcherEvent(account=account.username, new_videos=len(tasks), message=message)
            )
            logger.info("veille_nouveautes", compte=account.username, nombre=len(tasks))
        else:
            message = f"@{account.username} : aucune nouveauté."
            await self._bus.publish(
                WatcherEvent(account=account.username, new_videos=0, message=message)
            )
        return len(tasks)


class WatchOptionsFactory:
    """Construit les options d'un compte surveillé à partir des options globales."""

    @staticmethod
    def for_account(
        username: str, base: DownloadOptions, interval_minutes: int = 30
    ) -> WatchedAccount:
        return WatchedAccount(
            username=username,
            interval_minutes=interval_minutes,
            options=base.model_copy(deep=True),
        )


def monotonic_now() -> float:
    """Horloge monotone (utilisée par les tests de veille)."""
    return time.monotonic()
