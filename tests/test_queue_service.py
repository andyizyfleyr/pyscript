"""File d'attente : concurrence bornée, pause/reprise/annulation, réordonnancement, fin de lot."""

from __future__ import annotations

import asyncio

import pytest

from tikvault.domain.events import BatchFinished, EventBus, SourceResolved, TaskAdded
from tikvault.domain.models import DownloadOptions, DownloadTask, SourceType, TaskState
from tikvault.infrastructure.db.repository import SqlTaskRepository
from tikvault.services.queue_service import DownloadQueue
from tikvault.services.url_parser import ParsedSource

from .conftest import FakeExtractor, QueueFixture, RecordingNotifier


def source(video_id: str) -> ParsedSource:
    url = f"https://www.tiktok.com/@alice/video/{video_id}"
    return ParsedSource(SourceType.VIDEO, url, original=url)


def many(count: int) -> list[ParsedSource]:
    return [source(f"71{'0' * (17 - len(str(i)))}{i}") for i in range(count)]


class TestExecution:
    async def test_toutes_les_taches_sont_traitees(
        self,
        queue: tuple[DownloadQueue, FakeExtractor, RecordingNotifier],
        options: DownloadOptions,
    ) -> None:
        download_queue, extractor, _ = queue
        await download_queue.enqueue_sources(many(5), options)
        await download_queue.wait_idle(timeout=10)

        assert len(extractor.downloaded) == 5
        stats = await download_queue.compute_stats()
        assert stats.done == 5
        assert stats.total == 5

    async def test_evenements_d_ajout_publies(
        self,
        queue: tuple[DownloadQueue, FakeExtractor, RecordingNotifier],
        recorded_events: list[object],
        options: DownloadOptions,
    ) -> None:
        download_queue, _, _ = queue
        await download_queue.enqueue_sources(many(2), options)
        await download_queue.wait_idle(timeout=10)
        assert sum(1 for event in recorded_events if isinstance(event, TaskAdded)) == 2

    async def test_source_sans_interet(self, queue: QueueFixture, options: DownloadOptions) -> None:
        download_queue, _, _ = queue
        assert await download_queue.enqueue_sources([], options) == []


class TestConcurrence:
    async def test_jamais_plus_de_workers_que_prevu(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        options: DownloadOptions,
    ) -> None:
        """Vérifie que le nombre de téléchargements simultanés reste sous la limite."""
        simultanes = 0
        maximum = 0
        verrou = asyncio.Lock()

        class CountingExtractor(FakeExtractor):
            async def download(self, url, destination, options_, context, progress=None):
                nonlocal simultanes, maximum
                async with verrou:
                    simultanes += 1
                    maximum = max(maximum, simultanes)
                try:
                    await asyncio.sleep(0.03)
                    return await super().download(url, destination, options_, context, progress)
                finally:
                    async with verrou:
                        simultanes -= 1

        from tikvault.services.download_service import DownloadService
        from tikvault.services.source_resolver import SourceResolver

        extractor = CountingExtractor()
        limiter = DownloadOptions(**{**options.model_dump(), "max_concurrency": 2})
        resolver = SourceResolver(extractor=extractor, repository=repository, bus=event_bus)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        download_queue = DownloadQueue(
            repository=repository,
            resolver=resolver,
            download_service=service,
            bus=event_bus,
            stats_interval=0.05,
            output_dir=limiter.output_dir,
        )
        download_queue.set_concurrency(2)
        await download_queue.start()
        try:
            await download_queue.enqueue_sources(many(8), limiter)
            await download_queue.wait_idle(timeout=15)
        finally:
            await download_queue.shutdown(timeout=2)

        assert maximum <= 2, f"concurrence dépassée : {maximum} téléchargements simultanés"
        assert maximum >= 1

    async def test_ajustement_a_chaud(self, queue: QueueFixture) -> None:
        download_queue, _, _ = queue
        download_queue.set_concurrency(7)
        assert download_queue._concurrency == 7
        download_queue.set_concurrency(99)  # borné à 10
        assert download_queue._concurrency == 10
        download_queue.set_concurrency(0)  # borné à 1
        assert download_queue._concurrency == 1


class TestPauseReprise:
    async def test_pause_globale_bloque_les_workers(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        options: DownloadOptions,
    ) -> None:
        from tikvault.services.download_service import DownloadService
        from tikvault.services.source_resolver import SourceResolver

        extractor = FakeExtractor(delay=0.02)
        resolver = SourceResolver(extractor=extractor, repository=repository, bus=event_bus)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        download_queue = DownloadQueue(
            repository=repository,
            resolver=resolver,
            download_service=service,
            bus=event_bus,
            stats_interval=0.05,
            output_dir=options.output_dir,
        )
        download_queue.set_concurrency(2)
        await download_queue.start()
        try:
            await download_queue.pause_all()
            assert download_queue.is_paused
            await download_queue.enqueue_sources(many(4), options)
            await asyncio.sleep(0.3)
            assert len(extractor.downloaded) == 0, "rien ne doit être téléchargé pendant la pause"

            await download_queue.resume_all()
            toujours_en_pause: bool = download_queue.is_paused
            assert toujours_en_pause is False
            await download_queue.wait_idle(timeout=10)
            assert len(extractor.downloaded) == 4
        finally:
            await download_queue.shutdown(timeout=2)

    async def test_pause_individuelle_puis_reprise(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        options: DownloadOptions,
    ) -> None:
        from tikvault.services.download_service import DownloadService
        from tikvault.services.source_resolver import SourceResolver

        extractor = FakeExtractor()
        resolver = SourceResolver(extractor=extractor, repository=repository, bus=event_bus)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        download_queue = DownloadQueue(
            repository=repository,
            resolver=resolver,
            download_service=service,
            bus=event_bus,
            stats_interval=0.05,
            output_dir=options.output_dir,
        )
        await download_queue.start()
        try:
            # On met la file en pause avant d'ajouter : sinon la tâche est terminée
            # avant même que l'ordre de pause n'arrive (le téléchargement est instantané).
            await download_queue.pause_all()
            tasks = await download_queue.enqueue_sources(many(1), options)
            await download_queue.pause_task(tasks[0].id)
            await download_queue.wait_idle(timeout=10)

            paused = await repository.get_task(tasks[0].id)
            assert paused is not None
            assert paused.state is TaskState.PAUSED
            assert extractor.downloaded == []

            await download_queue.resume_task(tasks[0].id)
            await download_queue.resume_all()
            await download_queue.wait_idle(timeout=10)
            resumed = await repository.get_task(tasks[0].id)
            assert resumed is not None
            assert resumed.state is TaskState.DONE
            assert len(extractor.downloaded) == 1
        finally:
            await download_queue.shutdown(timeout=2)


class TestAnnulation:
    async def test_annulation_d_une_tache_en_file(
        self, queue: QueueFixture, options: DownloadOptions
    ) -> None:
        download_queue, _, _ = queue
        await download_queue.pause_all()  # on garde tout en file
        tasks = await download_queue.enqueue_sources(many(3), options)
        await download_queue.cancel_task(tasks[0].id)

        cancelled = (
            await download_queue.get_state(tasks[0].id)
            if hasattr(download_queue, "get_state")
            else None
        )
        _ = cancelled
        await download_queue.resume_all()
        await download_queue.wait_idle(timeout=10)
        stats = await download_queue.compute_stats()
        assert stats.cancelled == 1
        assert stats.done == 2

    async def test_annulation_globale(self, queue: QueueFixture, options: DownloadOptions) -> None:
        download_queue, extractor, _ = queue
        await download_queue.pause_all()
        await download_queue.enqueue_sources(many(4), options)
        await download_queue.cancel_all()
        await download_queue.resume_all()
        await asyncio.sleep(0.3)

        assert extractor.downloaded == []
        stats = await download_queue.compute_stats()
        assert stats.cancelled == 4


class TestReordonnancement:
    async def test_nouvel_ordre_pris_en_compte(
        self, queue: QueueFixture, options: DownloadOptions
    ) -> None:
        download_queue, _, _ = queue
        await download_queue.pause_all()
        tasks = await download_queue.enqueue_sources(many(3), options)

        inverse = [task.id for task in reversed(tasks)]
        await download_queue.reorder(inverse)

        persisted = [task.id for task in await download_queue._repository.list_tasks()]
        assert persisted == inverse

    async def test_position_persistee_en_base(
        self, queue: QueueFixture, options: DownloadOptions
    ) -> None:
        download_queue, _, _ = queue
        tasks = await download_queue.enqueue_sources(many(2), options)
        await download_queue.reorder([tasks[1].id, tasks[0].id])
        first = await download_queue._repository.get_task(tasks[1].id)
        assert first is not None
        assert first.position == 0


class TestRelanceEtPurge:
    async def test_relance_des_echecs(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        options: DownloadOptions,
    ) -> None:
        from tikvault.services.download_service import DownloadService
        from tikvault.services.source_resolver import SourceResolver

        url = "https://www.tiktok.com/@alice/video/7111111111111111111"
        extractor = FakeExtractor(fail_urls={url})
        resolver = SourceResolver(extractor=extractor, repository=repository, bus=event_bus)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        download_queue = DownloadQueue(
            repository=repository,
            resolver=resolver,
            download_service=service,
            bus=event_bus,
            stats_interval=0.05,
            output_dir=options.output_dir,
        )
        await download_queue.start()
        try:
            await download_queue.enqueue_sources([source("7111111111111111111")], options)
            await download_queue.wait_idle(timeout=10)
            stats = await download_queue.compute_stats()
            assert stats.failed == 1

            # On « répare » l'extracteur puis on relance.
            extractor.fail_urls = set()
            relaunched = await download_queue.retry_failed()
            assert relaunched == 1
            await download_queue.wait_idle(timeout=10)
            stats = await download_queue.compute_stats()
            assert stats.done == 1
            assert stats.failed == 0
        finally:
            await download_queue.shutdown(timeout=2)

    async def test_purge_des_taches_terminees(
        self, queue: QueueFixture, options: DownloadOptions
    ) -> None:
        download_queue, _, _ = queue
        await download_queue.enqueue_sources(many(2), options)
        await download_queue.wait_idle(timeout=10)

        removed = await download_queue.clear_finished()
        assert removed == 2
        stats = await download_queue.compute_stats()
        assert stats.total == 0


class TestFinDeLot:
    async def test_notification_et_evenement(
        self,
        queue: tuple[DownloadQueue, FakeExtractor, RecordingNotifier],
        recorded_events: list[object],
        options: DownloadOptions,
    ) -> None:
        download_queue, _, notifier = queue
        await download_queue.enqueue_sources(many(2), options)
        await download_queue.wait_idle(timeout=10)
        await asyncio.sleep(0.4)  # laisse la boucle de stats détecter la fin

        assert any(isinstance(event, BatchFinished) for event in recorded_events)
        assert len(notifier.messages) == 1
        assert "2 vidéo(s)" in notifier.messages[0][1]


class TestStatistiques:
    async def test_estimation_du_reste(self, queue: QueueFixture, options: DownloadOptions) -> None:
        download_queue, _, _ = queue
        await download_queue.enqueue_sources(many(3), options)
        await download_queue.wait_idle(timeout=10)
        stats = await download_queue.compute_stats()
        assert stats.done == 3
        assert stats.completion == pytest.approx(1.0)

    async def test_reprise_des_taches_en_file_apres_redemarrage(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        options: DownloadOptions,
    ) -> None:
        """Simule une fermeture brutale : les tâches en cours doivent repartir."""
        await repository.add_tasks(
            [
                DownloadTask(
                    url=f"https://www.tiktok.com/@a/video/{i}",
                    state=TaskState.DOWNLOADING,
                    options=options,
                )
                for i in range(2)
            ]
        )

        from tikvault.services.download_service import DownloadService
        from tikvault.services.source_resolver import SourceResolver

        extractor = FakeExtractor()
        resolver = SourceResolver(extractor=extractor, repository=repository, bus=event_bus)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        download_queue = DownloadQueue(
            repository=repository,
            resolver=resolver,
            download_service=service,
            bus=event_bus,
            stats_interval=0.05,
            output_dir=options.output_dir,
        )
        await download_queue.start()
        try:
            await download_queue.wait_idle(timeout=10)
            assert len(extractor.downloaded) == 2
        finally:
            await download_queue.shutdown(timeout=2)


class TestResolutionDeSource:
    async def test_profil_developpe_en_plusieurs_taches(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        recorded_events: list[object],
        options: DownloadOptions,
    ) -> None:
        from tikvault.domain.models import VideoMeta
        from tikvault.services.download_service import DownloadService
        from tikvault.services.source_resolver import SourceResolver

        playlist = [
            VideoMeta(video_id=str(7000 + i), url=f"https://www.tiktok.com/@alice/video/{7000 + i}")
            for i in range(4)
        ]
        extractor = FakeExtractor(playlist=playlist)
        resolver = SourceResolver(extractor=extractor, repository=repository, bus=event_bus)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        download_queue = DownloadQueue(
            repository=repository,
            resolver=resolver,
            download_service=service,
            bus=event_bus,
            stats_interval=0.05,
            output_dir=options.output_dir,
        )
        await download_queue.start()
        try:
            profil = ParsedSource(SourceType.PROFILE, "@alice", original="@alice")
            tasks = await download_queue.enqueue_sources([profil], options)
            assert len(tasks) == 4
            await download_queue.wait_idle(timeout=10)
            assert any(
                isinstance(event, SourceResolved) and event.count == 4 for event in recorded_events
            )
        finally:
            await download_queue.shutdown(timeout=2)
