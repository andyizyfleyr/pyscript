"""File de téléchargement : workers, concurrence bornée, pause/reprise/annulation.

Modèle d'exécution
------------------
* une :class:`asyncio.Queue` contient les identifiants de tâches à traiter ;
* N workers (N = concurrence réglable de 1 à 10) consomment cette file ;
* un événement global met tous les workers en pause ;
* un :class:`asyncio.Event` par tâche permet l'annulation coopérative ;
* l'état est persisté à chaque transition : un crash au milieu d'un lot ne perd rien
  (au redémarrage, :meth:`recover_interrupted` remet les tâches en file et yt-dlp
  reprend le fichier partiel là où il s'était arrêté).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Literal

import structlog

from tikvault.domain.errors import DownloadCancelled, classify_error
from tikvault.domain.events import (
    BatchFinished,
    EventBus,
    LogEvent,
    StatsUpdated,
    TaskAdded,
    TaskStateChanged,
)
from tikvault.domain.models import (
    BatchStats,
    DownloadOptions,
    DownloadTask,
    TaskState,
    utc_now,
)
from tikvault.domain.ports import Notifier, TaskRepository
from tikvault.services.disk import directory_size
from tikvault.services.source_resolver import SourceResolver
from tikvault.services.url_parser import ParsedSource

logger = structlog.get_logger(__name__)

Intent = Literal["run", "pause", "cancel"]


class DownloadQueue:
    """Ordonnance les téléchargements et expose les commandes utilisateur."""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        resolver: SourceResolver,
        download_service: object,
        bus: EventBus,
        notifier: Notifier | None = None,
        stats_interval: float = 1.0,
        output_dir: str = "~/TikVault",
    ) -> None:
        # ``download_service`` est typé lâchement pour éviter un cycle d'import ;
        # le contrat attendu est :meth:`DownloadService.execute(task, cancel)`.
        self._repository = repository
        self._resolver = resolver
        self._download_service = download_service
        self._bus = bus
        self._notifier = notifier
        self._stats_interval = stats_interval
        self._output_dir = output_dir

        self._pending: asyncio.Queue[str] = asyncio.Queue()
        # Identifiants déjà pris en charge par un worker : empêche qu'une même tâche
        # soit téléchargée deux fois quand plusieurs workers se réveillent ensemble.
        self._claimed: set[str] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._cancels: dict[str, asyncio.Event] = {}
        self._intents: dict[str, Intent] = {}
        self._active: set[str] = set()
        self._concurrency = 3
        self._pause_gate = asyncio.Event()
        self._pause_gate.set()  # non pausé au départ
        self._running = False
        self._stopped = asyncio.Event()
        self._batch_open = False
        self._batch_started_at: float | None = None
        self._batch_bytes = 0
        self._disk_used = 0
        self._stats_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Démarre les workers et réinjecte les tâches interrompues par un arrêt brutal."""
        if self._running:
            return
        await self._repository.init()
        recovered = await self._repository.recover_interrupted()
        if recovered:
            logger.info("taches_recuperees", nombre=recovered)
            await self._bus.publish(
                LogEvent(
                    level="info", message=f"{recovered} tâche(s) interrompue(s) remise(s) en file."
                )
            )

        self._running = True
        self._stopped.clear()
        await self._reload_pending()
        for index in range(self._concurrency):
            self._spawn_worker(index)
        self._stats_task = asyncio.create_task(self._stats_loop(), name="tikvault-stats")
        logger.info("file_demarree", concurrence=self._concurrency)

    async def shutdown(self, *, timeout: float = 10.0) -> None:
        """Arrêt propre : on laisse finir les écritures, pas les téléchargements."""
        self._running = False
        for cancel_event in self._cancels.values():
            cancel_event.set()  # interrompt les téléchargements en cours proprement
        if self._stats_task is not None:
            self._stats_task.cancel()
        for worker in self._workers:
            worker.cancel()
        pending: list[asyncio.Task[None]] = [*self._workers]
        if self._stats_task is not None:
            pending.append(self._stats_task)
        if pending:
            await asyncio.wait(pending, timeout=timeout)
        self._workers.clear()
        self._claimed.clear()
        self._stats_task = None
        self._stopped.set()
        logger.info("file_arretee")

    def _spawn_worker(self, index: int) -> None:
        task = asyncio.create_task(self._worker(index), name=f"tikvault-worker-{index}")
        self._workers.append(task)

    async def _reload_pending(self) -> None:
        """Recharge la file depuis la base (reprise après fermeture de l'application)."""
        queued = await self._repository.list_tasks(states=[TaskState.QUEUED], limit=100_000)
        for task in sorted(queued, key=lambda t: (t.position, t.created_at)):
            await self._pending.put(task.id)
        if queued:
            self._batch_open = True
            self._batch_started_at = time.monotonic()

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------
    def _stop_requested(self) -> bool:
        """Arrêt demandé ?

        Passer par une méthode (plutôt que lire l'attribut directement) évite que le
        vérificateur de types fige la valeur pour tout le corps de la boucle.
        """
        return not self._running

    async def _worker(self, index: int) -> None:
        while True:
            if self._stop_requested():
                return
            if index >= self._concurrency:
                # Concurrence réduite : ce worker surnuméraire se retire.
                return

            # On attend la reprise *avant* de piocher dans la file : pendant une pause,
            # le travail reste visible dans la file et aucun worker ne « retient » une tâche.
            await self._pause_gate.wait()
            if self._stop_requested():
                return

            try:
                task_id = await self._pending.get()
            except asyncio.CancelledError:
                return

            try:
                # La pause peut avoir été demandée pendant l'attente de travail :
                # on revérifie la porte avant de démarrer quoi que ce soit.
                await self._pause_gate.wait()
                if self._stop_requested():
                    return

                # Réclamation atomique : pas de ``await`` entre le test et l'ajout,
                # donc deux workers ne peuvent jamais partir sur la même tâche.
                if task_id in self._claimed:
                    continue
                self._claimed.add(task_id)
                try:
                    task = await self._repository.get_task(task_id)
                    if task is None or task.state is not TaskState.QUEUED:
                        continue  # annulée, en pause, déjà traitée ou doublon
                    await self._run(task)
                finally:
                    self._claimed.discard(task_id)
            except asyncio.CancelledError:
                return
            finally:
                self._pending.task_done()

    async def _run(self, task: DownloadTask) -> None:
        cancel = asyncio.Event()
        self._cancels[task.id] = cancel
        self._intents[task.id] = "run"
        self._active.add(task.id)
        self._output_dir = task.options.output_dir
        try:
            result = await self._download_service.execute(task, cancel)  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            raise
        except DownloadCancelled:
            result = task
            result.state = TaskState.CANCELLED
            await self._repository.save_task(result)
        except Exception as exc:
            logger.exception("erreur_worker", task=task.id)
            result = task
            result.state = TaskState.FAILED
            result.error_message = classify_error(exc).user_message
            await self._repository.save_task(result)
        finally:
            self._active.discard(task.id)
            self._cancels.pop(task.id, None)

        # Une « pause » se matérialise par une interruption du téléchargement :
        # on la distingue d'une vraie annulation pour pouvoir reprendre plus tard.
        if result.state is TaskState.CANCELLED and self._intents.get(task.id) == "pause":
            await self._mark_paused(result)

        self._intents.pop(task.id, None)

        if result.state is TaskState.DONE and result.file_size:
            self._batch_bytes += result.file_size

    async def _mark_paused(self, task: DownloadTask) -> None:
        previous = task.state
        task.state = TaskState.PAUSED
        task.updated_at = utc_now()
        task.finished_at = None
        await self._repository.save_task(task)
        await self._bus.publish(
            TaskStateChanged(task.id, previous, TaskState.PAUSED, "mise en pause par l'utilisateur")
        )

    # ------------------------------------------------------------------
    # Ajout de travail
    # ------------------------------------------------------------------
    async def enqueue_sources(
        self, sources: list[ParsedSource], options: DownloadOptions
    ) -> list[DownloadTask]:
        """Résout des sources puis les met en file. Renvoie les tâches créées."""
        if not sources:
            return []
        self._open_batch()
        created = await self._resolver.resolve(sources, options)
        for task in created:
            await self._pending.put(task.id)
            await self._bus.publish(TaskAdded(task=task))
        logger.info("taches_ajoutees", nombre=len(created))
        return created

    async def enqueue_tasks(self, tasks: list[DownloadTask]) -> None:
        """Met en file des tâches déjà créées (utilisé par la veille de comptes)."""
        if not tasks:
            return
        self._open_batch()
        await self._repository.add_tasks(tasks)
        for task in tasks:
            await self._pending.put(task.id)
            await self._bus.publish(TaskAdded(task=task))

    def _open_batch(self) -> None:
        if not self._batch_open:
            self._batch_open = True
            self._batch_started_at = time.monotonic()
            self._batch_bytes = 0

    # ------------------------------------------------------------------
    # Commandes utilisateur
    # ------------------------------------------------------------------
    async def pause_task(self, task_id: str) -> None:
        self._intents[task_id] = "pause"
        if cancel := self._cancels.get(task_id):
            cancel.set()  # interrompt le téléchargement ; le fichier partiel est conservé
            return
        task = await self._repository.get_task(task_id)
        if task is not None and task.state is TaskState.QUEUED:
            await self._mark_paused(task)

    async def resume_task(self, task_id: str) -> None:
        task = await self._repository.get_task(task_id)
        if task is None or task.state not in {
            TaskState.PAUSED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }:
            return
        self._intents[task_id] = "run"
        self._open_batch()
        task.state = TaskState.QUEUED
        task.attempts = 0
        task.error_message = None
        task.failure_kind = None
        task.finished_at = None
        task.updated_at = utc_now()
        await self._repository.save_task(task)
        await self._bus.publish(TaskStateChanged(task.id, TaskState.PAUSED, TaskState.QUEUED))
        # Si un worker tient déjà cet identifiant (bloqué sur la pause), il la traitera
        # dès la reprise : réinjecter créerait un doublon de téléchargement.
        if task.id not in self._claimed:
            await self._pending.put(task.id)

    async def cancel_task(self, task_id: str) -> None:
        self._intents[task_id] = "cancel"
        if cancel := self._cancels.get(task_id):
            cancel.set()
            return
        task = await self._repository.get_task(task_id)
        if task is None or task.state.is_terminal:
            return
        previous = task.state
        task.state = TaskState.CANCELLED
        task.finished_at = utc_now()
        await self._repository.save_task(task)
        await self._bus.publish(TaskStateChanged(task.id, previous, TaskState.CANCELLED, "annulée"))

    async def pause_all(self) -> None:
        self._pause_gate.clear()
        logger.info("file_en_pause")

    async def resume_all(self) -> None:
        self._pause_gate.set()
        # Les tâches mises en pause individuellement repartent aussi.
        paused = await self._repository.list_tasks(states=[TaskState.PAUSED], limit=100_000)
        for task in paused:
            await self.resume_task(task.id)

    async def cancel_all(self) -> None:
        active = list(self._active)
        for task_id in active:
            self._intents[task_id] = "cancel"
            if cancel := self._cancels.get(task_id):
                cancel.set()
        queued = await self._repository.list_tasks(states=[TaskState.QUEUED], limit=100_000)
        for task in queued:
            task.state = TaskState.CANCELLED
            task.finished_at = utc_now()
            await self._repository.save_task(task)
        # Vide la file mémoire (les tâches déjà réclamées sont arrêtées via leur événement).
        while not self._pending.empty():
            drained = self._pending.get_nowait()
            self._claimed.discard(drained)
            self._pending.task_done()

    async def retry_failed(self) -> int:
        """Remet en file toutes les tâches échouées. Renvoie leur nombre."""
        failed = await self._repository.list_tasks(states=[TaskState.FAILED], limit=100_000)
        for task in failed:
            await self.resume_task(task.id)
        return len(failed)

    async def reorder(self, ordered_ids: list[str]) -> None:
        """Applique un nouvel ordre (glisser-déposer dans l'UI)."""
        await self._repository.set_positions(ordered_ids)
        # On reconstruit la file mémoire : simple et sans risque d'incohérence.
        drained: list[str] = []
        while not self._pending.empty():
            drained.append(self._pending.get_nowait())
            self._pending.task_done()
        order = {task_id: rank for rank, task_id in enumerate(ordered_ids)}
        for task_id in sorted(drained, key=lambda t: order.get(t, len(order))):
            await self._pending.put(task_id)

    async def clear_finished(self) -> int:
        """Supprime de l'historique les tâches terminées (fait / ignoré / annulé)."""
        terminal = await self._repository.list_tasks(
            states=[TaskState.DONE, TaskState.SKIPPED, TaskState.CANCELLED, TaskState.FAILED],
            limit=100_000,
        )
        for task in terminal:
            await self._repository.delete_task(task.id)
        return len(terminal)

    def set_concurrency(self, value: int) -> None:
        """Ajuste la concurrence à chaud (1 à 10)."""
        value = max(1, min(10, value))
        if value == self._concurrency:
            return
        previous, self._concurrency = self._concurrency, value
        if value > previous:
            for index in range(previous, value):
                self._spawn_worker(index)
        logger.info("concurrence_modifiee", ancienne=previous, nouvelle=value)

    @property
    def output_dir(self) -> str:
        return self._output_dir

    @output_dir.setter
    def output_dir(self, value: str) -> None:
        self._output_dir = value

    @property
    def is_paused(self) -> bool:
        return not self._pause_gate.is_set()

    @property
    def active_count(self) -> int:
        return len(self._active)

    # ------------------------------------------------------------------
    # Statistiques
    # ------------------------------------------------------------------
    async def compute_stats(self) -> BatchStats:
        """Agrège les compteurs pour le tableau de bord."""
        raw = await self._repository.aggregate_stats()
        counts = {key: int(value) for key, value in raw.items()}

        stats = BatchStats(
            total=counts.get("total", 0),
            queued=counts.get(TaskState.QUEUED.value, 0),
            running=counts.get("_active", len(self._active)),
            paused=counts.get(TaskState.PAUSED.value, 0),
            done=counts.get(TaskState.DONE.value, 0),
            failed=counts.get(TaskState.FAILED.value, 0),
            cancelled=counts.get(TaskState.CANCELLED.value, 0),
            skipped=counts.get(TaskState.SKIPPED.value, 0),
            bytes_downloaded=counts.get("bytes_done", 0),
            disk_used=self._disk_used,
            speed_bps=await self._current_speed(),
        )
        if self._batch_started_at is not None:
            stats.elapsed_seconds = max(0.0, time.monotonic() - self._batch_started_at)

        # Estimation du reste : taille moyenne d'une vidéo terminée x tâches restantes.
        if stats.done > 0 and stats.bytes_downloaded > 0:
            average = stats.bytes_downloaded / stats.done
            stats.remaining_bytes = int(average * (stats.queued + stats.running + stats.paused))
        return stats

    async def _current_speed(self) -> float:
        """Somme des débits des tâches actives (lue en base, donc à jour)."""
        active = await self._repository.list_tasks(
            states=[TaskState.DOWNLOADING, TaskState.FETCHING, TaskState.POSTPROCESSING], limit=50
        )
        return sum(task.speed_bps for task in active)

    async def refresh_disk_usage(self) -> int:
        """Recalcule la taille du dossier de destination (coûteux : appelé périodiquement)."""
        self._disk_used = await asyncio.to_thread(
            directory_size, Path(self._output_dir).expanduser()
        )
        return self._disk_used

    async def _stats_loop(self) -> None:
        disk_tick = 0
        while self._running:
            try:
                await asyncio.sleep(self._stats_interval)
                disk_tick += 1
                if disk_tick >= 10:  # recalcul disque toutes les ~10 s
                    disk_tick = 0
                    await self.refresh_disk_usage()
                stats = await self.compute_stats()
                await self._bus.publish(StatsUpdated(stats=stats))
                await self._maybe_close_batch(stats)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("erreur_stats")

    async def _maybe_close_batch(self, stats: BatchStats) -> None:
        """Détecte la fin d'un lot et notifie l'utilisateur."""
        if not self._batch_open or not self.is_idle:
            return
        if stats.queued or stats.running:
            return

        self._batch_open = False
        elapsed = (time.monotonic() - self._batch_started_at) if self._batch_started_at else 0.0
        event = BatchFinished(
            done=stats.done,
            failed=stats.failed,
            skipped=stats.skipped,
            bytes_downloaded=self._batch_bytes,
            elapsed_seconds=elapsed,
        )
        await self._bus.publish(event)
        if self._notifier is not None:
            message = f"{stats.done} vidéo(s) téléchargée(s)"
            if stats.failed:
                message += f", {stats.failed} échec(s)"
            self._notifier.notify("TikVault — lot terminé", message)
        logger.info(
            "lot_termine", reussies=stats.done, echecs=stats.failed, secondes=round(elapsed, 1)
        )

    @property
    def is_idle(self) -> bool:
        """Aucun travail en cours d'exécution.

        Pendant une pause, aucun worker ne tourne : on considère la file inactive même
        s'il reste des tâches (elles attendent simplement la reprise).
        """
        if self._active:
            return False
        if self.is_paused:
            return True
        return not self._claimed and self._pending.empty()

    async def wait_idle(self, timeout: float | None = None) -> None:
        """Attend que la file soit vide (pratique en mode ligne de commande et dans les tests).

        Une tâche « réclamée » par un worker mais pas encore démarrée compte comme du
        travail en cours : sans cela, on pourrait rendre la main trop tôt.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.is_idle:
                return
            if deadline is not None and time.monotonic() > deadline:
                msg = "File toujours active après expiration du délai."
                raise TimeoutError(msg)
            await asyncio.sleep(0.02)


__all__ = ["DownloadQueue", "Intent"]
