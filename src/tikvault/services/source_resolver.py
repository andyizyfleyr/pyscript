"""Résolution des sources : transforme une entrée utilisateur en tâches concrètes.

Un profil, un hashtag ou un son peut représenter des centaines de vidéos : la résolution
se fait en tâche de fond, avec publication progressive des événements
(:class:`tikvault.domain.events.SourceResolved`).
"""

from __future__ import annotations

import asyncio

import structlog

from tikvault.domain import ports
from tikvault.domain.errors import TikVaultError, classify_error
from tikvault.domain.events import EventBus, LogEvent, SourceResolved
from tikvault.domain.models import DownloadOptions, DownloadTask, SourceType
from tikvault.services.url_parser import ParsedSource

logger = structlog.get_logger(__name__)

# Plafond de sécurité par source « large » (profil/hashtag) pour éviter un accident de 10 000 tâches.
DEFAULT_SOURCE_LIMIT = 1000


class SourceResolver:
    """Développe chaque :class:`ParsedSource` en tâches, avec dédoublonnage global."""

    def __init__(
        self,
        *,
        extractor: ports.VideoExtractor,
        repository: ports.TaskRepository,
        bus: EventBus,
        user_agents: ports.UserAgentProvider | None = None,
        cookies: ports.CookieProvider | None = None,
        default_limit: int = DEFAULT_SOURCE_LIMIT,
    ) -> None:
        self._extractor = extractor
        self._repository = repository
        self._bus = bus
        self._user_agents = user_agents
        self._cookies = cookies
        self._default_limit = default_limit

    async def resolve(
        self, sources: list[ParsedSource], options: DownloadOptions
    ) -> list[DownloadTask]:
        """Résout toutes les sources et crée les tâches correspondantes en base."""
        created: list[DownloadTask] = []
        seen_urls: set[str] = set()

        # On ignore aussi ce qui est déjà en file ou déjà téléchargé : coller deux fois
        # la même liste ne doit rien dupliquer.
        for source in sources:
            if source.dedupe_key in seen_urls:
                continue
            seen_urls.add(source.dedupe_key)
            try:
                created.extend(await self._resolve_one(source, options))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                classified = classify_error(exc)
                logger.warning("source_inexploitable", source=source.value, erreur=str(exc)[:200])
                await self._bus.publish(
                    LogEvent(
                        level="warning",
                        message=f"Source ignorée ({source.value}) : {classified.user_message}",
                    )
                )

        if created:
            await self._repository.add_tasks(created)
        return created

    async def _resolve_one(
        self, source: ParsedSource, options: DownloadOptions
    ) -> list[DownloadTask]:
        if source.kind is SourceType.VIDEO:
            task = self._make_task(source.value, SourceType.VIDEO, options)
            await self._bus.publish(SourceResolved(source.value, source.value, 1))
            return [task]

        limit = options.filters.max_items or self._default_limit
        context = ports.ExtractionContext(
            user_agent=self._user_agents.next() if self._user_agents else None,
            cookies_file=await self._cookies.export() if self._cookies else None,
            cookies_from_browser=self._cookies.browser_spec() if self._cookies else None,
            timeout=options.socket_timeout,
        )
        resolved = await self._extractor.resolve_source(
            source.value, source.kind, context, limit=limit
        )

        tasks: list[DownloadTask] = []
        for index, url in enumerate(resolved.urls, start=1):
            meta = resolved.metas[index - 1] if index - 1 < len(resolved.metas) else None
            task = self._make_task(url, resolved.source_type, options, index=index)
            if meta is not None:
                task.meta = meta
            tasks.append(task)

        await self._bus.publish(SourceResolved(source.value, resolved.label, len(tasks)))
        logger.info("source_resolue", source=source.value, videos=len(tasks))
        return tasks

    @staticmethod
    def _make_task(
        url: str,
        source_type: SourceType,
        options: DownloadOptions,
        *,
        index: int = 1,
    ) -> DownloadTask:
        return DownloadTask(
            url=url,
            source_type=source_type,
            position=index,
            max_attempts=options.max_attempts,
            # Copie profonde : les réglages changés plus tard ne modifient pas la file en cours.
            options=options.model_copy(deep=True),
        )


class TikVaultErrorGuard:
    """Petit utilitaire : formate une erreur de résolution pour l'UI."""

    @staticmethod
    def describe(exc: BaseException) -> str:
        if isinstance(exc, TikVaultError):
            return exc.user_message
        return classify_error(exc).user_message
