"""Orchestration du téléchargement d'**une** tâche.

Enchaînement : contexte réseau → métadonnées → filtres → anti-doublon → chemin de
destination → téléchargement (avec relances) → empreinte → persistance.

Ce service ne gère ni la concurrence ni l'ordre : c'est le rôle de
:class:`tikvault.services.queue_service.DownloadQueue`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from tikvault.domain import ports
from tikvault.domain.errors import DownloadCancelled, TikVaultError, classify_error
from tikvault.domain.events import (
    EventBus,
    TaskFailed,
    TaskMetaResolved,
    TaskProgress,
    TaskRetrying,
    TaskStateChanged,
)
from tikvault.domain.filters import VideoFilter
from tikvault.domain.models import DownloadTask, FailureKind, TaskState, VideoMeta, utc_now
from tikvault.services import filename
from tikvault.services.dedupe import DedupeService
from tikvault.services.disk import ensure_writable
from tikvault.services.progress import ProgressRelay
from tikvault.services.retry import RetryPolicy, retry_async

logger = structlog.get_logger(__name__)


class DownloadService:
    """Exécute une tâche de bout en bout, en émettant des événements sur le bus."""

    def __init__(
        self,
        *,
        extractor: ports.VideoExtractor,
        repository: ports.TaskRepository,
        bus: EventBus,
        proxies: ports.ProxyProvider | None = None,
        user_agents: ports.UserAgentProvider | None = None,
        cookies: ports.CookieProvider | None = None,
        dedupe: DedupeService | None = None,
    ) -> None:
        self._extractor = extractor
        self._repository = repository
        self._bus = bus
        self._proxies = proxies
        self._user_agents = user_agents
        self._cookies = cookies
        self._dedupe = dedupe or DedupeService(repository)
        self._loop = asyncio.get_running_loop()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------
    def configure(
        self,
        *,
        proxies: ports.ProxyProvider | None = None,
        user_agents: ports.UserAgentProvider | None = None,
        cookies: ports.CookieProvider | None = None,
    ) -> None:
        """Re-câble les fournisseurs réseau (appelé quand les réglages changent)."""
        if proxies is not None:
            self._proxies = proxies
        if user_agents is not None:
            self._user_agents = user_agents
        if cookies is not None:
            self._cookies = cookies

    async def execute(self, task: DownloadTask, cancel: asyncio.Event) -> DownloadTask:
        """Télécharge ``task`` et renvoie la tâche mise à jour (état terminal)."""
        options = task.options
        try:
            root = ensure_writable(Path(options.output_dir))
        except TikVaultError as exc:
            return await self._fail(task, exc, will_retry=False)

        await self._transition(task, TaskState.FETCHING)

        # 1. Métadonnées (nécessaires aux filtres, au nom de fichier et à la miniature).
        meta = None
        try:
            context = await self._build_context(task, cancel)
            meta = await self._extractor.probe(task.url, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Des métadonnées absentes ne sont pas fatales : on retente au téléchargement.
            logger.info("metadonnees_indisponibles", url=task.url, erreur=str(exc)[:160])

        if cancel.is_set():
            raise DownloadCancelled

        if meta is not None:
            task.meta = meta
            await self._bus.publish(TaskMetaResolved(task_id=task.id, meta=meta))
            await self._repository.save_task(task)

            # 2. Filtres métier.
            decision = VideoFilter(options.filters).evaluate(meta)
            if not decision.accepted:
                return await self._skip(task, f"filtrée ({decision.reason})")

            # 3. Anti-doublon rapide : même identifiant déjà téléchargé avec succès.
            if await self._is_duplicate(task, meta.video_id):
                return await self._skip(task, "doublon (déjà téléchargée)")

        # 4. Chemin de destination.
        try:
            relative = filename.render(
                options.filename_template, meta, url=task.url, options=options
            )
            destination = filename.resolve_destination(root, relative)
        except TikVaultError as exc:
            return await self._fail(task, exc, will_retry=False)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # 5. Téléchargement avec relances intelligentes.
        policy = RetryPolicy(max_attempts=task.max_attempts)
        outcome = await retry_async(
            lambda attempt: self._attempt(task, destination, cancel, attempt),
            policy=policy,
            on_retry=lambda exc, attempt, delay: self._on_retry(task, exc, attempt, delay),
        )

        if cancel.is_set() and not outcome.success:
            return await self._cancel(task)

        if not outcome.success:
            return await self._fail(task, outcome.error or TikVaultError(), will_retry=False)

        result = outcome.result
        assert result is not None  # garanti par outcome.success
        return await self._finalize(task, result, meta)

    # ------------------------------------------------------------------
    # Étapes internes
    # ------------------------------------------------------------------
    async def _build_context(
        self, task: DownloadTask, cancel: asyncio.Event
    ) -> ports.ExtractionContext:
        """Construit le contexte réseau d'une tentative (proxy, UA, cookies)."""
        proxy: str | None = None
        if self._proxies is not None and task.options.rotate_proxies and len(self._proxies) > 0:
            proxy = self._proxies.next()

        user_agent: str | None = None
        if self._user_agents is not None and task.options.rotate_user_agents:
            user_agent = self._user_agents.next()

        cookies_file: Path | None = None
        browser_spec: str | None = None
        if self._cookies is not None:
            cookies_file = await self._cookies.export()
            browser_spec = self._cookies.browser_spec()
            if cookies_file is not None:
                browser_spec = None  # le fichier explicite a priorité

        return ports.ExtractionContext(
            proxy=proxy,
            user_agent=user_agent,
            cookies_file=cookies_file,
            cookies_from_browser=browser_spec,
            timeout=task.options.socket_timeout,
            cancel=cancel,
        )

    async def _attempt(
        self, task: DownloadTask, destination: Path, cancel: asyncio.Event, attempt: int
    ) -> ports.DownloadResult:
        """Une tentative de téléchargement : nouveau contexte à chaque fois (proxy pivoté)."""
        task.attempts = attempt
        await self._transition(task, TaskState.DOWNLOADING)

        context = await self._build_context(task, cancel)
        relay = ProgressRelay(self._loop, self._bus, task.id)
        try:
            result = await self._extractor.download(
                task.url,
                destination,
                task.options,
                context,
                progress=relay,
            )
        except Exception:
            # On informe le pool de proxies pour qu'il mette ce relais en pause.
            if self._proxies is not None and context.proxy:
                self._proxies.report_failure(context.proxy)
            raise
        finally:
            relay.close()

        # Succès : le proxy a bien fonctionné, on améliore sa priorité.
        if self._proxies is not None and context.proxy:
            self._proxies.report_success(context.proxy)
        return result

    async def _finalize(
        self, task: DownloadTask, result: ports.DownloadResult, meta: VideoMeta | None
    ) -> DownloadTask:
        """Vérifie le fichier, calcule son empreinte, détecte les doublons de contenu."""
        await self._transition(task, TaskState.POSTPROCESSING)

        path = result.file_path
        if not path.exists():
            return await self._fail(
                task,
                TikVaultError("Le fichier de sortie est introuvable après téléchargement."),
                will_retry=False,
            )

        size = path.stat().st_size
        digest = await self._dedupe.hash_of(path)

        # Dédoublonnage strict : un même contenu sous un autre identifiant.
        # Attention : si l'ancienne tâche pointait sur ce même chemin (fichier supprimé
        # puis retéléchargé), ce n'est pas un doublon — c'est le fichier qu'on vient
        # d'écrire. On ne supprime donc jamais un fichier qui est notre propre sortie.
        existing = await self._repository.find_by_hash(digest)
        if (
            existing is not None
            and existing.id != task.id
            and existing.file_path != str(path)
            and Path(existing.file_path or "").exists()
        ):
            path.unlink(missing_ok=True)
            return await self._skip(task, f"doublon de contenu (identique à {existing.id[:8]})")

        task.state = TaskState.DONE
        task.file_path = str(path)
        task.file_hash = digest
        task.file_size = size
        task.bytes_done = size
        task.bytes_total = size or task.bytes_total
        task.progress = 1.0
        task.extractor = result.extractor
        task.error_message = None
        task.failure_kind = None
        task.finished_at = utc_now()
        task.updated_at = utc_now()
        if task.meta is None and result.meta is not None:
            task.meta = result.meta
        await self._repository.save_task(task)

        await self._bus.publish(
            TaskProgress(
                task_id=task.id,
                progress=1.0,
                bytes_done=size,
                bytes_total=size,
                speed_bps=0.0,
                eta_seconds=None,
                phase="terminé",
            )
        )
        await self._bus.publish(TaskStateChanged(task.id, TaskState.POSTPROCESSING, TaskState.DONE))
        logger.info("telechargement_reussi", task=task.id, fichier=path.name, octets=size)
        return task

    async def _is_duplicate(self, task: DownloadTask, video_id: str) -> bool:
        """Vérifie si cet identifiant a déjà été téléchargé avec succès (fichier encore présent)."""
        known = await self._repository.known_video_ids([video_id])
        if video_id not in known:
            return False
        # On ne bloque que si le fichier est réellement sur le disque.
        tasks = await self._repository.list_tasks(query=video_id, limit=5)
        for candidate in tasks:
            if candidate.id == task.id:
                continue
            if (
                candidate.state is TaskState.DONE
                and candidate.file_path
                and Path(candidate.file_path).exists()
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # Transitions d'état
    # ------------------------------------------------------------------
    async def _transition(self, task: DownloadTask, state: TaskState) -> None:
        previous = task.state
        if previous is state:
            return
        task.state = state
        task.updated_at = utc_now()
        if state.is_active and task.started_at is None:
            task.started_at = utc_now()
        await self._repository.save_task(task)
        await self._bus.publish(TaskStateChanged(task.id, previous, state))

    async def _skip(self, task: DownloadTask, reason: str) -> DownloadTask:
        previous = task.state
        task.state = TaskState.SKIPPED
        task.skip_reason = reason
        task.finished_at = utc_now()
        task.updated_at = utc_now()
        await self._repository.save_task(task)
        await self._bus.publish(TaskStateChanged(task.id, previous, TaskState.SKIPPED, reason))
        logger.info("tache_ignoree", task=task.id, raison=reason)
        return task

    async def _fail(
        self, task: DownloadTask, error: BaseException, *, will_retry: bool
    ) -> DownloadTask:
        classified = classify_error(error)
        previous = task.state
        task.state = TaskState.FAILED
        task.failure_kind = FailureKind(classified.kind)
        task.error_message = classified.user_message
        task.finished_at = utc_now()
        task.updated_at = utc_now()
        await self._repository.save_task(task)
        await self._bus.publish(
            TaskStateChanged(task.id, previous, TaskState.FAILED, task.error_message)
        )
        await self._bus.publish(
            TaskFailed(
                task_id=task.id,
                kind=task.failure_kind.value,
                message=task.error_message or classified.user_message,
                will_retry=will_retry,
                attempt=task.attempts,
            )
        )
        logger.warning(
            "tache_echouee", task=task.id, erreur=classified.kind.value, detail=str(error)[:200]
        )
        return task

    async def _cancel(self, task: DownloadTask) -> DownloadTask:
        previous = task.state
        task.state = TaskState.CANCELLED
        task.finished_at = utc_now()
        task.updated_at = utc_now()
        await self._repository.save_task(task)
        await self._bus.publish(TaskStateChanged(task.id, previous, TaskState.CANCELLED, "annulée"))
        return task

    async def _on_retry(
        self, task: DownloadTask, error: BaseException, attempt: int, delay: float
    ) -> None:
        classified = classify_error(error)
        await self._bus.publish(
            TaskRetrying(
                task_id=task.id,
                attempt=attempt + 1,
                delay_seconds=delay,
                reason=classified.user_message,
            )
        )
