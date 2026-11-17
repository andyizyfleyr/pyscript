"""Pont entre l'interface Qt (thread graphique) et le moteur asyncio (thread dédié).

Deux règles strictes :
  1. le moteur tourne dans **son propre thread** avec sa propre boucle asyncio —
     l'interface ne bloque jamais pendant un téléchargement ;
  2. la communication ne se fait que par **signaux Qt** (moteur → UI) et par
     ``run_coroutine_threadsafe`` (UI → moteur). Aucun objet Qt ne traverse vers le
     moteur, aucun objet asyncio ne bloque l'UI.

Le :class:`Container` est construit **dans** le thread moteur, car certains services
captent la boucle courante à l'initialisation.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from tikvault.app import Container
from tikvault.domain.events import (
    BatchFinished,
    EngineEvent,
    LogEvent,
    SourceResolved,
    StatsUpdated,
    TaskAdded,
    TaskCompleted,
    TaskFailed,
    TaskMetaResolved,
    TaskProgress,
    TaskRetrying,
    TaskStateChanged,
    UpdateAvailable,
    WatcherEvent,
)
from tikvault.domain.models import BatchStats, DownloadOptions, DownloadTask, TaskState
from tikvault.infrastructure.paths import AppPaths
from tikvault.services.url_parser import ParsedSource
from tikvault.settings import AppSettings


class EngineBridge(QObject):
    """Façade Qt du moteur : signaux sortants, méthodes entrantes."""

    # --- Événements moteur → interface -----------------------------------
    task_added = Signal(object)  # DownloadTask complet
    task_state_changed = Signal(str, str, str)  # id, nouvel état, message
    task_meta = Signal(str, object)  # id, VideoMeta (titre, auteur, miniature…)
    task_completed = Signal(object)  # DownloadTask complet (état terminal)
    tasks_loaded = Signal(list)  # list[DownloadTask] — réponse à request_tasks()
    task_progress = Signal(str, float, float, str)  # id, ratio, débit, phase
    stats_updated = Signal(object)  # BatchStats
    batch_finished = Signal(int, int, int, int)  # réussi, échecs, ignorés, octets
    source_resolved = Signal(str, str, int)  # source, libellé, nb vidéos
    watcher_tick = Signal(str, int, str)  # compte, nouveautés, message
    log_message = Signal(str, str)  # niveau, message
    update_available = Signal(str, str)  # version actuelle, dernière
    thumbnail_ready = Signal(str, str)  # url vidéo, chemin local
    watched_loaded = Signal(list)  # list[WatchedAccount]
    engine_ready = Signal()
    engine_error = Signal(str)

    def __init__(
        self,
        *,
        settings: AppSettings,
        paths: AppPaths,
        notifier: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._paths = paths
        self._notifier = notifier
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._container: Container | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    @property
    def container(self) -> Container | None:
        return self._container

    def start(self) -> None:
        """Lance le thread moteur (non bloquant)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="tikvault-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Arrête proprement le moteur et attend la fin du thread."""
        self._stop_event.set()
        if self._loop is not None and self._container is not None:
            asyncio.run_coroutine_threadsafe(self._container.stop(), self._loop)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        self._loop = None

    def _run(self) -> None:
        """Corps du thread moteur : crée la boucle, le conteneur, puis attend l'arrêt."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._container = Container(
                settings=self._settings, paths=self._paths, notifier=self._notifier
            )
            loop.run_until_complete(self._container.start())
            self._container.bus.subscribe_all(self._on_event)
            self.engine_ready.emit()
            loop.run_until_complete(self._idle())
        except Exception as exc:
            self.engine_error.emit(f"Le moteur s'est arrêté : {exc}")
        finally:
            try:
                if self._container is not None:
                    loop.run_until_complete(self._container.stop())
            except Exception:
                pass
            loop.close()
            self._loop = None

    async def _idle(self) -> None:
        """Attend que l'interface demande l'arrêt."""
        while not self._stop_event.is_set():
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------------
    # Réception des événements moteur
    # ------------------------------------------------------------------
    def _on_event(self, event: EngineEvent) -> None:
        """Appelé dans le thread moteur : on se contente d'émettre des signaux Qt."""
        match event:
            case TaskAdded(task=task):
                self.task_added.emit(task)
            case TaskStateChanged(task_id=tid, current=current, message=message):
                self.task_state_changed.emit(tid, current.value, message or "")
            case TaskProgress(task_id=tid, progress=progress, speed_bps=speed, phase=phase):
                self.task_progress.emit(tid, progress, speed, phase)
            case TaskMetaResolved(task_id=tid, meta=meta):
                self.task_meta.emit(tid, meta)
            case TaskCompleted(task=task):
                self.task_completed.emit(task)
            case TaskFailed(task_id=tid, kind=kind, message=message):
                self.task_state_changed.emit(tid, TaskState.FAILED.value, f"[{kind}] {message}")
            case TaskRetrying(task_id=tid, attempt=attempt, delay_seconds=delay, reason=reason):
                self.log_message.emit(
                    "info", f"Nouvelle tentative {attempt} dans {delay:.0f} s — {reason}"
                )
                self.task_progress.emit(tid, -1.0, 0.0, f"relance ({attempt})")
            case StatsUpdated(stats=stats):
                self.stats_updated.emit(stats)
            case BatchFinished(done=done, failed=failed, skipped=skipped, bytes_downloaded=size):
                self.batch_finished.emit(done, failed, skipped, size)
            case SourceResolved(source=source, label=label, count=count):
                self.source_resolved.emit(source, label, count)
            case WatcherEvent(account=account, new_videos=count, message=message):
                self.watcher_tick.emit(account, count, message)
            case LogEvent(level=level, message=message):
                self.log_message.emit(level, message)
            case UpdateAvailable(current_version=current, latest_version=latest):
                self.update_available.emit(current, latest)
            case _:
                pass

    # ------------------------------------------------------------------
    # Interface → moteur
    # ------------------------------------------------------------------
    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any] | None:
        """Programme une coroutine sur la boucle moteur. Renvoie son future."""
        if self._loop is None:
            self.engine_error.emit("Le moteur n'est pas encore démarré.")
            return None
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    @Slot(str, object)
    def add_sources(self, text: str, file_path: str | None = None) -> None:
        """Analyse le texte (et/ou un fichier) puis met les tâches en file."""
        self.add_sources_with_options(text, file_path, None)

    def add_sources_with_options(
        self, text: str, file_path: str | None, options: DownloadOptions | None
    ) -> None:
        """Même chose, avec des options de lot explicites (boîte de dialogue d'ajout)."""
        if self._container is None:
            self.engine_error.emit("Le moteur n'est pas encore démarré.")
            return

        async def _run() -> None:
            if self._container is None:
                return
            sources: list[ParsedSource] = self._container.parser.parse_text(text)
            if file_path:
                sources.extend(self._container.parser.parse_file(Path(file_path)))
            # Dédoublonnage global (même vidéo collée via deux sources différentes).
            unique: dict[str, ParsedSource] = {}
            for source in sources:
                unique.setdefault(source.dedupe_key, source)
            ordered = list(unique.values())
            if not ordered:
                await self._container.bus.publish(
                    LogEvent(
                        level="warning", message="Aucune source TikTok reconnue dans la saisie."
                    )
                )
                return
            chosen = options or self._container.settings.download_options()
            created = await self._container.queue.enqueue_sources(ordered, chosen)
            await self._container.bus.publish(
                LogEvent(level="info", message=f"{len(created)} tâche(s) ajoutée(s) à la file.")
            )

        self.submit(_run())

    @Slot(str)
    def pause_task(self, task_id: str) -> None:
        if self._container:
            self.submit(self._container.queue.pause_task(task_id))

    @Slot(str)
    def resume_task(self, task_id: str) -> None:
        if self._container:
            self.submit(self._container.queue.resume_task(task_id))

    @Slot(str)
    def cancel_task(self, task_id: str) -> None:
        if self._container:
            self.submit(self._container.queue.cancel_task(task_id))

    @Slot()
    def pause_all(self) -> None:
        if self._container:
            self.submit(self._container.queue.pause_all())

    @Slot()
    def resume_all(self) -> None:
        if self._container:
            self.submit(self._container.queue.resume_all())

    @Slot()
    def cancel_all(self) -> None:
        if self._container:
            self.submit(self._container.queue.cancel_all())

    @Slot()
    def retry_failed(self) -> None:
        if self._container:
            self.submit(self._container.queue.retry_failed())

    @Slot(list)
    def reorder(self, ordered_ids: list[str]) -> None:
        if self._container:
            self.submit(self._container.queue.reorder(ordered_ids))

    @Slot(int)
    def set_concurrency(self, value: int) -> None:
        if self._container:
            self._container.queue.set_concurrency(value)

    @Slot(object)
    def apply_settings(self, settings: AppSettings) -> None:
        if self._container:
            self._container.apply_settings(settings)

    @Slot(str, str)
    def add_watched_account(self, username: str, interval: str) -> None:
        if self._container is None:
            return
        from tikvault.domain.models import WatchedAccount

        account = WatchedAccount(
            username=username,
            interval_minutes=max(1, int(interval or 30)),
            options=self._container.settings.download_options(),
        )
        self.submit(self._container.watcher.add_account(account))

    @Slot(str)
    def remove_watched_account(self, username: str) -> None:
        if self._container:
            self.submit(self._container.watcher.remove_account(username))

    @Slot()
    def check_now(self) -> None:
        if self._container:
            self._container.watcher.wake_up()

    @Slot(str)
    def request_thumbnail(self, url: str) -> None:
        """Demande une miniature ; émet ``thumbnail_ready`` quand elle est en cache."""
        container = self._container
        if container is None:
            return

        async def _run() -> None:
            path = await container.thumbnails.fetch(url)
            if path is not None:
                self.thumbnail_ready.emit(url, str(path))

        self.submit(_run())

    @Slot(str)
    def export_report(self, destination: str) -> None:
        container = self._container
        if container is None:
            return

        async def _run() -> None:
            path = await container.reports.export(Path(destination))
            await container.bus.publish(LogEvent(level="info", message=f"Rapport exporté : {path}"))

        self.submit(_run())

    @Slot()
    def check_updates(self) -> None:
        container = self._container
        if container is None:
            return

        async def _run() -> None:
            latest = await container.check_updates()
            if latest is None:
                await container.bus.publish(LogEvent(level="info", message="yt-dlp est à jour."))

        self.submit(_run())

    @Slot()
    def apply_update(self) -> None:
        container = self._container
        if container is None:
            return

        async def _run() -> None:
            success, message = await container.update_engine()
            await container.bus.publish(
                LogEvent(level="info" if success else "warning", message=message)
            )

        self.submit(_run())

    @Slot()
    def request_tasks(self, states: list[str] | None = None, query: str = "") -> None:
        """Charge des tâches et émet ``tasks_loaded`` (retour dans le thread graphique)."""

        async def _run() -> None:
            if self._container is None:
                return
            wanted = [TaskState(value) for value in states] if states else None
            tasks = await self._container.repository.list_tasks(
                states=wanted, query=query or None, limit=2000
            )
            self.tasks_loaded.emit(tasks)

        self.submit(_run())

    def request_watched(self) -> None:
        """Charge la liste des comptes surveillés et émet ``watched_loaded``."""

        async def _run() -> None:
            if self._container is None:
                return
            self.watched_loaded.emit(await self._container.repository.list_watched())

        self.submit(_run())

    def load_stats(self) -> BatchStats | None:
        """Statistiques courantes (lecture bloquante courte, utilisée à l'ouverture)."""
        if self._container is None or self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(self._container.queue.compute_stats(), self._loop)
        try:
            return future.result(timeout=3.0)
        except (TimeoutError, Exception):
            return None


__all__ = ["DownloadOptions", "DownloadTask", "EngineBridge"]
