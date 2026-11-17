"""Export du rapport de téléchargement au format CSV.

Le fichier est écrit en UTF-8 avec BOM : Excel (Windows) ouvre sinon les accents
et les titres arabes/coréens en charabia.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from tikvault.domain.models import DownloadTask, TaskState
from tikvault.domain.ports import TaskRepository

COLUMNS = (
    ("id", "Identifiant"),
    ("url", "URL"),
    ("source", "Type de source"),
    ("etat", "État"),
    ("titre", "Titre"),
    ("auteur", "Auteur"),
    ("date_publication", "Date de publication"),
    ("duree_s", "Durée (s)"),
    ("vues", "Vues"),
    ("fichier", "Fichier"),
    ("taille_o", "Taille (octets)"),
    ("empreinte", "Empreinte BLAKE2b"),
    ("extracteur", "Extracteur"),
    ("tentatives", "Tentatives"),
    ("erreur", "Erreur"),
    ("cree_le", "Ajouté le"),
    ("termine_le", "Terminé le"),
)

_STATE_LABELS = {
    TaskState.DONE: "réussi",
    TaskState.FAILED: "échec",
    TaskState.SKIPPED: "ignoré",
    TaskState.CANCELLED: "annulé",
    TaskState.PAUSED: "en pause",
    TaskState.QUEUED: "en file",
    TaskState.DOWNLOADING: "en cours",
    TaskState.FETCHING: "résolution",
    TaskState.POSTPROCESSING: "post-traitement",
}


def _format(dt: datetime | None) -> str:
    return dt.astimezone().isoformat(timespec="seconds") if dt else ""


def task_to_row(task: DownloadTask) -> dict[str, str]:
    """Convertit une tâche en ligne CSV (libellés en français)."""
    meta = task.meta
    return {
        "id": task.id,
        "url": task.url,
        "source": task.source_type.value,
        "etat": _STATE_LABELS.get(task.state, task.state.value),
        "titre": meta.display_title if meta else "",
        "auteur": (meta.uploader or meta.uploader_id) if meta else "",
        "date_publication": meta.upload_date.isoformat() if meta and meta.upload_date else "",
        "duree_s": f"{meta.duration:.1f}" if meta and meta.duration else "",
        "vues": str(meta.view_count) if meta and meta.view_count is not None else "",
        "fichier": task.file_path or "",
        "taille_o": str(task.file_size or ""),
        "empreinte": task.file_hash or "",
        "extracteur": task.extractor or "",
        "tentatives": str(task.attempts),
        "erreur": task.error_message or task.skip_reason or "",
        "cree_le": _format(task.created_at),
        "termine_le": _format(task.finished_at),
    }


class ReportService:
    """Génère des rapports CSV à partir de l'historique persisté."""

    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def export(
        self,
        destination: Path,
        *,
        states: list[TaskState] | None = None,
        query: str | None = None,
        limit: int = 100_000,
    ) -> Path:
        """Écrit le rapport et renvoie son chemin."""
        tasks = await self._repository.list_tasks(states=states, query=query, limit=limit)
        return self.write(tasks, destination)

    @staticmethod
    def write(tasks: list[DownloadTask], destination: Path) -> Path:
        """Écrit une liste de tâches en CSV (séparateur point-virgule, compatible Excel FR)."""
        destination = destination.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[key for key, _ in COLUMNS],
                delimiter=";",
                extrasaction="ignore",
            )
            # Ligne d'en-tête française.
            handle.write(";".join(label for _, label in COLUMNS) + "\n")
            for task in tasks:
                writer.writerow(task_to_row(task))
        return destination
