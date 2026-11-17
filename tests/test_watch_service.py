"""Veille de comptes : détection des nouveautés, absence de doublons, tolérance aux pannes."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from tikvault.domain.events import EngineEvent, EventBus, WatcherEvent
from tikvault.domain.models import (
    DownloadOptions,
    DownloadTask,
    SourceType,
    VideoMeta,
    WatchedAccount,
    utc_now,
)
from tikvault.infrastructure.db.repository import SqlTaskRepository
from tikvault.services.watch_service import AccountWatcher

from .conftest import FakeExtractor


class FileAttente:
    """File d'attente factice qui mémorise ce qu'on lui confie."""

    def __init__(self) -> None:
        self.recus: list[DownloadTask] = []

    async def enqueue_tasks(self, tasks: list[DownloadTask]) -> None:
        self.recus.extend(tasks)


def make_meta(video_id: str) -> VideoMeta:
    return VideoMeta(video_id=video_id, url=f"https://www.tiktok.com/@alice/video/{video_id}")


def build(
    repository: SqlTaskRepository,
    bus: EventBus,
    *,
    playlist: list[VideoMeta],
    probe_error: Exception | None = None,
) -> tuple[AccountWatcher, FakeExtractor, FileAttente]:
    extractor = FakeExtractor(playlist=playlist, probe_error=probe_error)
    file_attente = FileAttente()
    watcher = AccountWatcher(
        repository=repository,
        extractor=extractor,
        queue=file_attente,
        bus=bus,
        tick_seconds=3600,
    )
    return watcher, extractor, file_attente


class TestVerification:
    async def test_premier_passage_mets_tout_en_file(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        playlist = [make_meta(str(1000 + i)) for i in range(3)]
        watcher, _, file_attente = build(repository, event_bus, playlist=playlist)
        await repository.upsert_watched(WatchedAccount(username="alice", options=options))

        nouvelles = await watcher.check_account((await repository.list_watched())[0])

        assert nouvelles == 3
        assert len(file_attente.recus) == 3
        assert all(task.source_type is SourceType.PROFILE for task in file_attente.recus)

    async def test_second_passage_sans_nouveaute(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        playlist = [make_meta("1000"), make_meta("1001")]
        watcher, _, file_attente = build(repository, event_bus, playlist=playlist)
        account = WatchedAccount(username="alice", options=options)
        await repository.upsert_watched(account)

        assert await watcher.check_account(account) == 2
        assert await watcher.check_account(account) == 0
        assert len(file_attente.recus) == 2

    async def test_nouvelle_video_detectee(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        playlist = [make_meta("1000")]
        watcher, extractor, file_attente = build(repository, event_bus, playlist=playlist)
        account = WatchedAccount(username="alice", options=options)
        await repository.upsert_watched(account)
        await watcher.check_account(account)

        extractor.playlist.append(make_meta("2000"))
        assert await watcher.check_account(account) == 1
        assert file_attente.recus[-1].meta is not None
        assert file_attente.recus[-1].meta.video_id == "2000"

    async def test_identifiants_vus_persistes(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, _ = build(repository, event_bus, playlist=[make_meta("1000")])
        account = WatchedAccount(username="alice", options=options)
        await repository.upsert_watched(account)
        await watcher.check_account(account)

        reloaded = (await repository.list_watched())[0]
        assert "1000" in reloaded.last_video_ids
        assert reloaded.last_checked_at is not None

    async def test_evenement_publie(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        vus: list[WatcherEvent] = []

        def noter(event: EngineEvent) -> None:
            if isinstance(event, WatcherEvent):
                vus.append(event)

        event_bus.subscribe(WatcherEvent, noter)
        watcher, _, _ = build(repository, event_bus, playlist=[make_meta("1000")])
        account = WatchedAccount(username="alice", options=options)
        await repository.upsert_watched(account)

        await watcher.check_account(account)

        assert len(vus) == 1
        assert vus[0].new_videos == 1

    async def test_echec_reseau_ne_casse_pas_la_veille(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, file_attente = build(
            repository, event_bus, playlist=[], probe_error=RuntimeError("profil injoignable")
        )
        account = WatchedAccount(username="alice", options=options)
        await repository.upsert_watched(account)

        assert await watcher.check_account(account) == 0
        assert file_attente.recus == []
        # Le compte reste surveillé et l'horodatage est à jour.
        reloaded = (await repository.list_watched())[0]
        assert reloaded.enabled
        assert reloaded.last_checked_at is not None


class TestPlanification:
    async def test_compte_pas_encore_echu_ignorer(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, file_attente = build(repository, event_bus, playlist=[make_meta("1000")])
        account = WatchedAccount(
            username="alice", interval_minutes=60, options=options, last_checked_at=utc_now()
        )
        await repository.upsert_watched(account)

        assert await watcher.check_all() == 0
        assert file_attente.recus == []

    async def test_compte_echu_verifie(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, _ = build(repository, event_bus, playlist=[make_meta("1000")])
        account = WatchedAccount(
            username="alice",
            interval_minutes=1,
            options=options,
            last_checked_at=utc_now() - timedelta(minutes=5),
        )
        await repository.upsert_watched(account)
        assert await watcher.check_all() == 1

    async def test_compte_desactive_ignorer(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, _ = build(repository, event_bus, playlist=[make_meta("1000")])
        await repository.upsert_watched(
            WatchedAccount(username="alice", enabled=False, options=options)
        )
        assert await watcher.check_all() == 0


class TestGestionDesComptes:
    async def test_ajout_et_suppression(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, _ = build(repository, event_bus, playlist=[])
        await watcher.add_account(WatchedAccount(username="@alice", options=options))
        assert len(await repository.list_watched()) == 1

        await watcher.remove_account("alice")
        assert await repository.list_watched() == []

    async def test_activation_desactivation(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, _ = build(repository, event_bus, playlist=[])
        await watcher.add_account(WatchedAccount(username="alice", options=options))
        await watcher.set_enabled("alice", False)
        assert (await repository.list_watched())[0].enabled is False
        await watcher.set_enabled("alice", True)
        assert (await repository.list_watched())[0].enabled is True


class TestBoucle:
    async def test_demarrage_et_arret(
        self, repository: SqlTaskRepository, event_bus: EventBus
    ) -> None:
        watcher, _, _ = build(repository, event_bus, playlist=[])
        await watcher.start()
        assert watcher.running
        await asyncio.sleep(0.05)
        await watcher.stop()
        assert not watcher.running

    async def test_arret_sans_demarrage(
        self, repository: SqlTaskRepository, event_bus: EventBus
    ) -> None:
        watcher, _, _ = build(repository, event_bus, playlist=[])
        await watcher.stop()  # ne doit rien lever

    async def test_reveil_force(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        watcher, _, file_attente = build(repository, event_bus, playlist=[make_meta("1000")])
        await repository.upsert_watched(WatchedAccount(username="alice", options=options))
        await watcher.start()
        try:
            watcher.wake_up()
            for _ in range(50):
                if file_attente.recus:
                    break
                await asyncio.sleep(0.05)
            assert len(file_attente.recus) == 1
        finally:
            await watcher.stop()
