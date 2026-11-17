"""Dépôt SQLite : persistance, recherche, reprise après crash, dédoublonnage."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tikvault.domain.models import (
    DownloadOptions,
    DownloadTask,
    FailureKind,
    SourceType,
    TaskState,
    VideoMeta,
    WatchedAccount,
    utc_now,
)
from tikvault.infrastructure.db.repository import SqlTaskRepository


def make_task(**kwargs: object) -> DownloadTask:
    base: dict[str, object] = {
        "url": "https://www.tiktok.com/@alice/video/7111111111111111111",
        "options": DownloadOptions(),
    }
    base.update(kwargs)
    return DownloadTask(**base)  # type: ignore[arg-type]


class TestCrud:
    async def test_ajout_et_lecture(self, repository: SqlTaskRepository) -> None:
        task = make_task()
        await repository.add_tasks([task])
        loaded = await repository.get_task(task.id)
        assert loaded is not None
        assert loaded.url == task.url
        assert loaded.state is TaskState.QUEUED

    async def test_lecture_inconnue(self, repository: SqlTaskRepository) -> None:
        assert await repository.get_task("inexistant") is None

    async def test_mise_a_jour(self, repository: SqlTaskRepository) -> None:
        task = make_task()
        await repository.add_tasks([task])
        task.state = TaskState.DONE
        task.file_path = "/tmp/video.mp4"
        task.file_size = 1234
        await repository.save_task(task)

        loaded = await repository.get_task(task.id)
        assert loaded is not None
        assert loaded.state is TaskState.DONE
        assert loaded.file_size == 1234

    async def test_options_persistees(self, repository: SqlTaskRepository) -> None:
        options = DownloadOptions(max_concurrency=7, audio_only=True, audio_format="m4a")
        task = make_task(options=options)
        await repository.add_tasks([task])
        loaded = await repository.get_task(task.id)
        assert loaded is not None
        assert loaded.options.max_concurrency == 7
        assert loaded.options.audio_format == "m4a"

    async def test_options_corrompues_replient_sur_les_defauts(
        self, repository: SqlTaskRepository
    ) -> None:
        task = make_task()
        await repository.add_tasks([task])
        # On écrit volontairement un JSON invalide pour simuler une base abîmée.
        async with repository._db.session() as session:
            from sqlalchemy import update

            from tikvault.infrastructure.db.models import TaskRecord

            await session.execute(
                update(TaskRecord).where(TaskRecord.id == task.id).values(options_json="{invalide")
            )
        loaded = await repository.get_task(task.id)
        assert loaded is not None
        assert loaded.options.max_concurrency == DownloadOptions().max_concurrency

    async def test_suppression(self, repository: SqlTaskRepository) -> None:
        task = make_task()
        await repository.add_tasks([task])
        await repository.delete_task(task.id)
        assert await repository.get_task(task.id) is None


class TestRequetes:
    async def test_filtre_par_etat(self, repository: SqlTaskRepository) -> None:
        await repository.add_tasks(
            [
                make_task(url="https://www.tiktok.com/@a/video/1", state=TaskState.DONE),
                make_task(url="https://www.tiktok.com/@a/video/2", state=TaskState.FAILED),
                make_task(url="https://www.tiktok.com/@a/video/3", state=TaskState.QUEUED),
            ]
        )
        done = await repository.list_tasks(states=[TaskState.DONE])
        assert len(done) == 1
        assert done[0].state is TaskState.DONE

    async def test_recherche_textuelle(self, repository: SqlTaskRepository) -> None:
        task = make_task()
        task.meta = VideoMeta(
            video_id="7111", url=task.url, title="Recette de pâtes", uploader_id="chef"
        )
        await repository.add_tasks([task, make_task(url="https://www.tiktok.com/@b/video/9")])

        assert len(await repository.list_tasks(query="pâtes")) == 1
        assert len(await repository.list_tasks(query="chef")) == 1
        assert len(await repository.list_tasks(query="inexistant")) == 0

    async def test_filtre_par_type_erreur(self, repository: SqlTaskRepository) -> None:
        task = make_task(state=TaskState.FAILED, failure_kind=FailureKind.PRIVATE)
        await repository.add_tasks([task])
        assert len(await repository.list_tasks(failure_kind=FailureKind.PRIVATE)) == 1
        assert len(await repository.list_tasks(failure_kind=FailureKind.NETWORK)) == 0

    async def test_comptage(self, repository: SqlTaskRepository) -> None:
        await repository.add_tasks(
            [make_task(url=f"https://www.tiktok.com/@a/video/{i}") for i in range(5)]
        )
        assert await repository.count_tasks() == 5
        assert await repository.count_tasks(states=[TaskState.QUEUED]) == 5

    async def test_reordonnancement(self, repository: SqlTaskRepository) -> None:
        tasks = [make_task(url=f"https://www.tiktok.com/@a/video/{i}") for i in range(3)]
        await repository.add_tasks(tasks)
        reversed_ids = [task.id for task in reversed(tasks)]
        await repository.set_positions(reversed_ids)

        ordered = await repository.list_tasks()
        assert [task.id for task in ordered] == reversed_ids


class TestRepriseApresCrash:
    async def test_les_taches_en_cours_reviennent_en_file(
        self, repository: SqlTaskRepository
    ) -> None:
        tasks = [
            make_task(url="https://www.tiktok.com/@a/video/1", state=TaskState.DOWNLOADING),
            make_task(url="https://www.tiktok.com/@a/video/2", state=TaskState.FETCHING),
            make_task(url="https://www.tiktok.com/@a/video/3", state=TaskState.POSTPROCESSING),
            make_task(url="https://www.tiktok.com/@a/video/4", state=TaskState.DONE),
            make_task(url="https://www.tiktok.com/@a/video/5", state=TaskState.FAILED),
        ]
        await repository.add_tasks(tasks)

        recovered = await repository.recover_interrupted()
        assert recovered == 3
        queued = await repository.list_tasks(states=[TaskState.QUEUED])
        assert len(queued) == 3
        assert all(task.state is TaskState.QUEUED for task in queued)


class TestStatistiques:
    async def test_agregats(self, repository: SqlTaskRepository) -> None:
        done = make_task(
            url="https://www.tiktok.com/@a/video/1", state=TaskState.DONE, file_size=1000
        )
        await repository.add_tasks(
            [done, make_task(url="https://www.tiktok.com/@a/video/2", state=TaskState.FAILED)]
        )
        stats = await repository.aggregate_stats()
        assert stats["total"] == 2
        assert stats[TaskState.DONE.value] == 1
        assert stats[TaskState.FAILED.value] == 1
        assert stats["bytes_done"] == 1000


class TestDedoublonnage:
    async def test_identifiants_connus(self, repository: SqlTaskRepository) -> None:
        task = make_task(state=TaskState.DONE)
        task.meta = VideoMeta(video_id="7111", url=task.url)
        await repository.add_tasks([task])

        assert await repository.known_video_ids(["7111", "9999"]) == {"7111"}

    async def test_identifiant_en_echec_ignoré(self, repository: SqlTaskRepository) -> None:
        task = make_task(state=TaskState.FAILED)
        task.meta = VideoMeta(video_id="7111", url=task.url)
        await repository.add_tasks([task])
        assert await repository.known_video_ids(["7111"]) == set()

    async def test_recherche_par_empreinte(self, repository: SqlTaskRepository) -> None:
        task = make_task(state=TaskState.DONE, file_hash="abc123")
        await repository.add_tasks([task])
        found = await repository.find_by_hash("abc123")
        assert found is not None
        assert found.id == task.id
        assert await repository.find_by_hash("autre") is None


class TestComptesSurveilles:
    async def test_ajout_et_lecture(
        self, repository: SqlTaskRepository, options: DownloadOptions
    ) -> None:
        account = WatchedAccount(username="@alice", interval_minutes=15, options=options)
        await repository.upsert_watched(account)

        loaded = await repository.list_watched()
        assert len(loaded) == 1
        assert loaded[0].username == "alice"
        assert loaded[0].interval_minutes == 15

    async def test_mise_a_jour_sans_duplication(
        self, repository: SqlTaskRepository, options: DownloadOptions
    ) -> None:
        account = WatchedAccount(username="alice", options=options)
        await repository.upsert_watched(account)
        account.interval_minutes = 90
        account.last_video_ids = ["1", "2", "3"]
        await repository.upsert_watched(account)

        loaded = await repository.list_watched()
        assert len(loaded) == 1
        assert loaded[0].interval_minutes == 90
        assert loaded[0].last_video_ids == ["1", "2", "3"]

    async def test_filtre_actifs(
        self, repository: SqlTaskRepository, options: DownloadOptions
    ) -> None:
        await repository.upsert_watched(WatchedAccount(username="alice", options=options))
        await repository.upsert_watched(
            WatchedAccount(username="bob", enabled=False, options=options)
        )
        assert len(await repository.list_watched(enabled_only=True)) == 1

    async def test_suppression(
        self, repository: SqlTaskRepository, options: DownloadOptions
    ) -> None:
        await repository.upsert_watched(WatchedAccount(username="alice", options=options))
        await repository.delete_watched("@alice")
        assert await repository.list_watched() == []

    @pytest.mark.asyncio
    async def test_dates_renormalisees_en_utc(
        self, repository: SqlTaskRepository, options: DownloadOptions
    ) -> None:
        """SQLite perd le fuseau : la comparaison avec utc_now() doit rester possible."""
        account = WatchedAccount(username="alice", options=options, last_checked_at=utc_now())
        await repository.upsert_watched(account)
        loaded = (await repository.list_watched())[0]
        assert loaded.last_checked_at is not None
        assert loaded.last_checked_at.tzinfo is not None
        assert loaded.last_checked_at + timedelta(minutes=1) > utc_now()


class TestSourceType:
    async def test_type_de_source_persisté(self, repository: SqlTaskRepository) -> None:
        task = make_task(source_type=SourceType.PROFILE)
        await repository.add_tasks([task])
        loaded = await repository.get_task(task.id)
        assert loaded is not None
        assert loaded.source_type is SourceType.PROFILE
