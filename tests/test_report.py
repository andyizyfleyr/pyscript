"""Export CSV du rapport de téléchargement."""

from __future__ import annotations

import csv
from datetime import UTC, date
from pathlib import Path

import pytest

from tikvault.domain.models import (
    DownloadTask,
    FailureKind,
    SourceType,
    TaskState,
    VideoMeta,
    utc_now,
)
from tikvault.infrastructure.db.repository import SqlTaskRepository
from tikvault.services.report_service import ReportService, task_to_row


def make_task(**kwargs: object) -> DownloadTask:
    base: dict[str, object] = {"url": "https://www.tiktok.com/@alice/video/7111"}
    base.update(kwargs)
    return DownloadTask(**base)  # type: ignore[arg-type]


@pytest.fixture
def finished_task() -> DownloadTask:
    task = make_task(
        state=TaskState.DONE, file_size=1_000_000, file_hash="ab" * 16, extractor="yt-dlp"
    )
    task.meta = VideoMeta(
        video_id="7111",
        url=task.url,
        title="Vidéo d'été — 2024",
        uploader="Alice Dupont",
        uploader_id="alice",
        duration=42.5,
        upload_date=date(2024, 7, 15),
        view_count=123456,
    )
    return task


class TestLigne:
    def test_libelles_francais(self, finished_task: DownloadTask) -> None:
        row = task_to_row(finished_task)
        assert row["etat"] == "réussi"
        assert row["titre"] == "Vidéo d'été — 2024"
        assert row["auteur"] == "Alice Dupont"
        assert row["date_publication"] == "2024-07-15"
        assert row["vues"] == "123456"
        assert row["taille_o"] == "1000000"

    def test_erreur_remontee(self) -> None:
        task = make_task(
            state=TaskState.FAILED,
            failure_kind=FailureKind.PRIVATE,
            error_message="Vidéo privée.",
        )
        assert task_to_row(task)["erreur"] == "Vidéo privée."

    def test_motif_d_ignorance_remonte(self) -> None:
        task = make_task(state=TaskState.SKIPPED, skip_reason="doublon")
        assert task_to_row(task)["erreur"] == "doublon"

    def test_sans_metadonnees(self) -> None:
        row = task_to_row(make_task())
        assert row["titre"] == ""
        assert row["duree_s"] == ""


class TestExport:
    async def test_fichier_ecrit(
        self, repository: SqlTaskRepository, tmp_path: Path, finished_task: DownloadTask
    ) -> None:
        await repository.add_tasks([finished_task])
        service = ReportService(repository)
        chemin = await service.export(tmp_path / "rapport.csv")

        assert chemin.exists()
        contenu = chemin.read_text(encoding="utf-8-sig")
        assert "Titre" in contenu
        assert "Vidéo d'été" in contenu

    async def test_separateur_point_virgule(
        self, repository: SqlTaskRepository, tmp_path: Path, finished_task: DownloadTask
    ) -> None:
        await repository.add_tasks([finished_task])
        chemin = await ReportService(repository).export(tmp_path / "r.csv")
        lignes = chemin.read_text(encoding="utf-8-sig").splitlines()
        assert ";" in lignes[0]
        assert lignes[0].startswith("Identifiant;URL")

    async def test_bom_pour_excel(
        self, repository: SqlTaskRepository, tmp_path: Path, finished_task: DownloadTask
    ) -> None:
        await repository.add_tasks([finished_task])
        chemin = await ReportService(repository).export(tmp_path / "r.csv")
        assert chemin.read_bytes().startswith(b"\xef\xbb\xbf")

    async def test_filtre_par_etat(
        self, repository: SqlTaskRepository, tmp_path: Path, finished_task: DownloadTask
    ) -> None:
        await repository.add_tasks(
            [
                finished_task,
                make_task(url="https://www.tiktok.com/@a/video/9", state=TaskState.FAILED),
            ]
        )
        chemin = await ReportService(repository).export(
            tmp_path / "r.csv", states=[TaskState.FAILED]
        )
        lignes = chemin.read_text(encoding="utf-8-sig").splitlines()
        assert len(lignes) == 2  # en-tête + une ligne

    async def test_export_vide(self, repository: SqlTaskRepository, tmp_path: Path) -> None:
        chemin = await ReportService(repository).export(tmp_path / "vide.csv")
        assert len(chemin.read_text(encoding="utf-8-sig").splitlines()) == 1

    def test_ecriture_directe(self, tmp_path: Path, finished_task: DownloadTask) -> None:
        chemin = ReportService.write([finished_task], tmp_path / "sous" / "r.csv")
        assert chemin.exists()
        with chemin.open(encoding="utf-8-sig", newline="") as handle:
            lignes = list(csv.reader(handle, delimiter=";"))
        assert lignes[0][4] == "Titre"
        assert len(lignes) == 2

    async def test_donnees_lisibles_par_un_lecteur_csv(
        self, repository: SqlTaskRepository, tmp_path: Path, finished_task: DownloadTask
    ) -> None:
        await repository.add_tasks([finished_task])
        chemin = await ReportService(repository).export(tmp_path / "r.csv")
        with chemin.open(encoding="utf-8-sig", newline="") as handle:
            lignes = list(csv.DictReader(handle, delimiter=";"))
        assert lignes[0]["État"] == "réussi"
        assert lignes[0]["Vues"] == "123456"

    async def test_horodatages_localises(
        self, repository: SqlTaskRepository, tmp_path: Path
    ) -> None:
        task = make_task(created_at=utc_now().replace(tzinfo=UTC))
        await repository.add_tasks([task])
        chemin = await ReportService(repository).export(tmp_path / "r.csv")
        with chemin.open(encoding="utf-8-sig", newline="") as handle:
            ligne = next(csv.DictReader(handle, delimiter=";"))
        assert ligne is not None
        assert "Ajouté le" in ligne
        assert ligne["Ajouté le"] != ""


class TestTypeSource:
    async def test_type_de_source_dans_le_rapport(
        self, repository: SqlTaskRepository, tmp_path: Path
    ) -> None:
        await repository.add_tasks([make_task(source_type=SourceType.PROFILE)])
        chemin = await ReportService(repository).export(tmp_path / "r.csv")
        with chemin.open(encoding="utf-8-sig", newline="") as handle:
            ligne = next(csv.DictReader(handle, delimiter=";"))
        assert ligne["Type de source"] == "profile"
