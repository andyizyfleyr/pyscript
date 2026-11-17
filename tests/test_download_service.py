"""Service de téléchargement : métadonnées, filtres, doublons, échecs, annulation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tikvault.domain.errors import DownloadCancelled
from tikvault.domain.events import (
    EventBus,
    TaskFailed,
    TaskMetaResolved,
    TaskProgress,
    TaskStateChanged,
)
from tikvault.domain.models import (
    DownloadOptions,
    DownloadTask,
    FailureKind,
    FilterOptions,
    TaskState,
    VideoMeta,
)
from tikvault.infrastructure.db.repository import SqlTaskRepository
from tikvault.services.download_service import DownloadService

from .conftest import VIDEO_URL, FakeExtractor


async def run(
    service: DownloadService, task: DownloadTask, cancel: asyncio.Event | None = None
) -> DownloadTask:
    cancel = cancel or asyncio.Event()
    return await service.execute(task, cancel)


class TestSucces:
    async def test_fichier_produit_et_persiste(
        self, download_service: tuple[DownloadService, FakeExtractor], options: DownloadOptions
    ) -> None:
        service, extractor = download_service
        task = DownloadTask(url=VIDEO_URL, options=options)

        result = await run(service, task)

        assert result.state is TaskState.DONE
        assert result.file_path is not None
        assert Path(result.file_path).exists()
        assert result.file_size > 0
        assert result.extractor == "fake"

        persisted = Path(result.file_path)
        assert persisted.parent.name == "alice", (
            "le modèle {username}/... doit créer un sous-dossier"
        )
        assert extractor.downloaded == [VIDEO_URL]

    async def test_empreinte_de_contenu_calculee(
        self, download_service: tuple[DownloadService, FakeExtractor], options: DownloadOptions
    ) -> None:
        service, _ = download_service
        result = await run(service, DownloadTask(url=VIDEO_URL, options=options))
        assert result.file_hash is not None
        assert len(result.file_hash) == 32  # BLAKE2b tronqué à 16 octets

    async def test_metadonnees_enregistrees(
        self,
        download_service: tuple[DownloadService, FakeExtractor],
        repository: SqlTaskRepository,
        options: DownloadOptions,
    ) -> None:
        service, _ = download_service
        task = DownloadTask(url=VIDEO_URL, options=options)
        await repository.add_tasks([task])
        await run(service, task)

        loaded = await repository.get_task(task.id)
        assert loaded is not None
        assert loaded.meta is not None
        assert loaded.meta.uploader_id == "alice"
        assert loaded.meta.view_count == 120_000

    async def test_evenements_publies(
        self,
        download_service: tuple[DownloadService, FakeExtractor],
        recorded_events: list[object],
        options: DownloadOptions,
    ) -> None:
        service, _ = download_service
        task = DownloadTask(url=VIDEO_URL, options=options)
        await run(service, task)

        types = [type(event) for event in recorded_events]
        assert TaskMetaResolved in types
        assert TaskStateChanged in types
        assert TaskProgress in types
        assert TaskFailed not in types

    async def test_progression_terminee_a_cent_pourcent(
        self,
        download_service: tuple[DownloadService, FakeExtractor],
        options: DownloadOptions,
    ) -> None:
        service, _ = download_service
        result = await run(service, DownloadTask(url=VIDEO_URL, options=options))
        assert result.progress == 1.0


class TestRelances:
    async def test_relance_apres_429(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        extractor = FakeExtractor(fail_times=2)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        task = DownloadTask(url=VIDEO_URL, max_attempts=4, options=options)

        result = await run(service, task)

        assert result.state is TaskState.DONE
        assert extractor.attempts[VIDEO_URL] == 3
        assert result.attempts == 3

    async def test_abandon_apres_trop_de_tentatives(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        extractor = FakeExtractor(fail_times=10)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        task = DownloadTask(url=VIDEO_URL, max_attempts=3, options=options)

        result = await run(service, task)

        assert result.state is TaskState.FAILED
        assert result.failure_kind is FailureKind.RATE_LIMITED
        assert extractor.attempts[VIDEO_URL] == 3


class TestErreursClassees:
    async def test_video_indisponible(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        extractor = FakeExtractor(fail_urls={VIDEO_URL})
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)

        result = await run(service, DownloadTask(url=VIDEO_URL, max_attempts=3, options=options))

        assert result.state is TaskState.FAILED
        assert result.failure_kind is FailureKind.DELETED
        assert result.error_message is not None
        assert "supprimée" in result.error_message

    async def test_erreur_reseau_message_clair(
        self, repository: SqlTaskRepository, event_bus: EventBus, options: DownloadOptions
    ) -> None:
        class TimeoutExtractor(FakeExtractor):
            async def download(self, url, destination, options_, context, progress=None):
                msg = "The read operation timed out"
                raise RuntimeError(msg)

        service = DownloadService(
            extractor=TimeoutExtractor(), repository=repository, bus=event_bus
        )
        result = await run(service, DownloadTask(url=VIDEO_URL, max_attempts=1, options=options))

        assert result.state is TaskState.FAILED
        assert result.failure_kind is FailureKind.NETWORK

    async def test_echec_publie_un_evenement(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        recorded_events: list[object],
        options: DownloadOptions,
    ) -> None:
        extractor = FakeExtractor(fail_urls={VIDEO_URL})
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        await run(service, DownloadTask(url=VIDEO_URL, max_attempts=1, options=options))

        failures = [event for event in recorded_events if isinstance(event, TaskFailed)]
        assert len(failures) == 1
        assert failures[0].kind == FailureKind.DELETED.value


class TestFiltres:
    async def test_video_filtree_non_telechargee(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        tmp_path: Path,
    ) -> None:
        extractor = FakeExtractor()
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        options = DownloadOptions(
            output_dir=str(tmp_path), filters=FilterOptions(min_views=10_000_000)
        )

        result = await run(service, DownloadTask(url=VIDEO_URL, options=options))

        assert result.state is TaskState.SKIPPED
        assert result.skip_reason is not None
        assert "vues" in result.skip_reason
        assert extractor.downloaded == [], "une vidéo filtrée ne doit rien télécharger"

    async def test_duree_filtree(
        self, repository: SqlTaskRepository, event_bus: EventBus, tmp_path: Path
    ) -> None:
        service = DownloadService(extractor=FakeExtractor(), repository=repository, bus=event_bus)
        options = DownloadOptions(output_dir=str(tmp_path), filters=FilterOptions(min_duration=300))
        result = await run(service, DownloadTask(url=VIDEO_URL, options=options))
        assert result.state is TaskState.SKIPPED


class TestDedoublonnage:
    async def test_meme_identifiant_deja_telecharge(
        self,
        repository: SqlTaskRepository,
        event_bus: EventBus,
        tmp_path: Path,
    ) -> None:
        extractor = FakeExtractor()
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        options = DownloadOptions(output_dir=str(tmp_path))

        first = await run(service, DownloadTask(url=VIDEO_URL, options=options))
        assert first.state is TaskState.DONE

        second = await run(service, DownloadTask(url=VIDEO_URL, options=options))
        assert second.state is TaskState.SKIPPED
        assert second.skip_reason is not None
        assert "doublon" in second.skip_reason
        assert len(extractor.downloaded) == 1

    async def test_fichier_supprime_permet_un_nouveau_telechargement(
        self, repository: SqlTaskRepository, event_bus: EventBus, tmp_path: Path
    ) -> None:
        extractor = FakeExtractor()
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        options = DownloadOptions(output_dir=str(tmp_path))

        first = await run(service, DownloadTask(url=VIDEO_URL, options=options))
        assert first.file_path is not None
        Path(first.file_path).unlink()

        second = await run(service, DownloadTask(url=VIDEO_URL, options=options))
        assert second.state is TaskState.DONE

    async def test_contenu_identique_sous_autre_identifiant(
        self, repository: SqlTaskRepository, event_bus: EventBus, tmp_path: Path
    ) -> None:
        """Deux vidéos différentes qui contiennent exactement les mêmes octets."""
        meme_contenu = b"octets-identiques"
        extractor = FakeExtractor(
            content_for={
                "https://www.tiktok.com/@a/video/1": meme_contenu,
                "https://www.tiktok.com/@b/video/2": meme_contenu,
            }
        )
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        options = DownloadOptions(output_dir=str(tmp_path))

        first = await run(
            service, DownloadTask(url="https://www.tiktok.com/@a/video/1", options=options)
        )
        second = await run(
            service, DownloadTask(url="https://www.tiktok.com/@b/video/2", options=options)
        )

        assert first.state is TaskState.DONE
        assert second.state is TaskState.SKIPPED
        assert second.skip_reason is not None
        assert "doublon de contenu" in second.skip_reason


class TestAnnulation:
    async def test_annulation_avant_demarrage(
        self, download_service: tuple[DownloadService, FakeExtractor], options: DownloadOptions
    ) -> None:
        service, extractor = download_service
        cancel = asyncio.Event()
        cancel.set()
        task = DownloadTask(url=VIDEO_URL, options=options)

        with pytest.raises(DownloadCancelled):
            await service.execute(task, cancel)
        assert extractor.downloaded == []

    async def test_annulation_pendant_le_telechargement(
        self, repository: SqlTaskRepository, event_bus: EventBus, tmp_path: Path
    ) -> None:
        extractor = FakeExtractor(delay=0.05)
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        cancel = asyncio.Event()

        class CancelExtracteur(FakeExtractor):
            async def download(self, url, destination, options_, context, progress=None):
                assert context.cancel is cancel
                raise DownloadCancelled

        service = DownloadService(
            extractor=CancelExtracteur(), repository=repository, bus=event_bus
        )
        task = DownloadTask(
            url=VIDEO_URL, max_attempts=1, options=DownloadOptions(output_dir=str(tmp_path))
        )
        result = await service.execute(task, cancel)

        assert result.state in {TaskState.CANCELLED, TaskState.FAILED}


class TestMetadonneesManquantes:
    async def test_le_telechargement_continue_sans_probe(
        self, repository: SqlTaskRepository, event_bus: EventBus, tmp_path: Path
    ) -> None:
        """Si la résolution des métadonnées échoue, on tente quand même le téléchargement."""
        extractor = FakeExtractor(probe_error=RuntimeError("page inaccessible"))
        service = DownloadService(extractor=extractor, repository=repository, bus=event_bus)
        options = DownloadOptions(output_dir=str(tmp_path))

        result = await run(service, DownloadTask(url=VIDEO_URL, options=options))

        assert result.state is TaskState.DONE
        assert result.file_path is not None
        # Sans métadonnées, le chemin retombe sur des valeurs sûres.
        assert Path(result.file_path).name.endswith(".mp4")


class TestVideoMeta:
    def test_titre_par_defaut(self) -> None:
        meta = VideoMeta(video_id="42", url="u")
        assert meta.display_title == "vidéo 42"

    def test_titre_renseigne(self) -> None:
        meta = VideoMeta(video_id="42", url="u", title="  Ma vidéo  ")
        assert meta.display_title == "Ma vidéo"
