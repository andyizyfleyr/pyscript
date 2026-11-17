"""Dépôt SQLite : implémente le port :class:`tikvault.domain.ports.TaskRepository`.

Point de vigilance SQLite : le type ``DateTime(timezone=True)`` est stocké sans fuseau.
Toutes les dates sont donc re-normalisées en UTC conscient à la lecture
(:func:`_aware`), sinon les comparaisons avec ``utc_now()`` lèveraient ``TypeError``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, update

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
from tikvault.infrastructure.db.engine import Database
from tikvault.infrastructure.db.models import TaskRecord, WatchedRecord

# États considérés comme « interrompus » lors d'un arrêt brutal.
_INTERRUPTED = (TaskState.DOWNLOADING, TaskState.FETCHING, TaskState.POSTPROCESSING)


def _aware(value: datetime | None) -> datetime | None:
    """Remet un fuseau UTC sur une date naive lue depuis SQLite."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _meta_of(record: TaskRecord) -> VideoMeta | None:
    if not record.video_id:
        return None
    return VideoMeta(
        video_id=record.video_id,
        url=record.url,
        title=record.title or "",
        uploader=record.uploader or "",
        uploader_id=record.uploader_id or "",
        duration=record.duration,
        upload_date=record.upload_date.date() if record.upload_date else None,
        view_count=record.view_count,
        thumbnail=record.thumbnail,
    )


def _to_domain(record: TaskRecord) -> DownloadTask:
    try:
        options = DownloadOptions.model_validate_json(record.options_json or "{}")
    except Exception:
        options = DownloadOptions()

    return DownloadTask(
        id=record.id,
        url=record.url,
        source_type=SourceType(record.source_type),
        state=TaskState(record.state),
        position=record.position,
        progress=record.progress,
        bytes_done=record.bytes_done,
        bytes_total=record.bytes_total,
        speed_bps=record.speed_bps,
        eta_seconds=record.eta_seconds,
        attempts=record.attempts,
        max_attempts=record.max_attempts,
        failure_kind=FailureKind(record.failure_kind) if record.failure_kind else None,
        error_message=record.error_message,
        skip_reason=record.skip_reason,
        file_path=record.file_path,
        file_hash=record.file_hash,
        file_size=record.file_size,
        extractor=record.extractor,
        meta=_meta_of(record),
        created_at=_aware(record.created_at) or utc_now(),
        updated_at=_aware(record.updated_at) or utc_now(),
        started_at=_aware(record.started_at),
        finished_at=_aware(record.finished_at),
        options=options,
    )


def _apply(task: DownloadTask, record: TaskRecord) -> TaskRecord:
    """Recopie une tâche domaine dans un enregistrement ORM."""
    record.url = task.url
    record.source_type = task.source_type.value
    record.state = task.state.value
    record.position = task.position
    record.progress = task.progress
    record.bytes_done = task.bytes_done
    record.bytes_total = task.bytes_total
    record.speed_bps = task.speed_bps
    record.eta_seconds = task.eta_seconds
    record.attempts = task.attempts
    record.max_attempts = task.max_attempts
    record.failure_kind = task.failure_kind.value if task.failure_kind else None
    record.error_message = task.error_message
    record.skip_reason = task.skip_reason
    record.file_path = task.file_path
    record.file_hash = task.file_hash
    record.file_size = task.file_size
    record.extractor = task.extractor
    if task.meta is not None:
        record.video_id = task.meta.video_id
        record.title = task.meta.title
        record.uploader = task.meta.uploader
        record.uploader_id = task.meta.uploader_id
        record.duration = task.meta.duration
        record.view_count = task.meta.view_count
        record.thumbnail = task.meta.thumbnail
        if task.meta.upload_date is not None:
            record.upload_date = datetime.combine(
                task.meta.upload_date, datetime.min.time(), tzinfo=UTC
            )
    record.options_json = task.options.model_dump_json()
    record.created_at = task.created_at
    record.updated_at = task.updated_at
    record.started_at = task.started_at
    record.finished_at = task.finished_at
    return record


class SqlTaskRepository:
    """Persistance SQLite des tâches, de l'historique et des comptes surveillés."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def init(self) -> None:
        await self._db.create_all()

    async def close(self) -> None:
        await self._db.dispose()

    # ------------------------------------------------------------------
    # Tâches
    # ------------------------------------------------------------------
    async def add_tasks(self, tasks: Sequence[DownloadTask]) -> None:
        if not tasks:
            return
        async with self._db.session() as session:
            for task in tasks:
                session.add(_apply(task, TaskRecord(id=task.id)))

    async def get_task(self, task_id: str) -> DownloadTask | None:
        async with self._db.session() as session:
            record = await session.get(TaskRecord, task_id)
            return _to_domain(record) if record else None

    async def save_task(self, task: DownloadTask) -> None:
        """Insère ou met à jour (upsert) — appelé à chaque transition et à chaque tick."""
        async with self._db.session() as session:
            record = await session.get(TaskRecord, task.id)
            if record is None:
                session.add(_apply(task, TaskRecord(id=task.id)))
            else:
                _apply(task, record)

    def _base_select(
        self,
        *,
        states: Iterable[TaskState] | None,
        query: str | None,
        failure_kind: FailureKind | None,
    ) -> Select[Any]:
        # ``Select[Any]`` : le paramètre générique de Select varie selon la version de
        # SQLAlchemy (tuple ou entité) ; on ne dépend d'aucune des deux formes ici.
        statement = select(TaskRecord)
        if states:
            statement = statement.where(TaskRecord.state.in_([s.value for s in states]))
        if failure_kind is not None:
            statement = statement.where(TaskRecord.failure_kind == failure_kind.value)
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    TaskRecord.title.ilike(pattern),
                    TaskRecord.uploader_id.ilike(pattern),
                    TaskRecord.uploader.ilike(pattern),
                    TaskRecord.url.ilike(pattern),
                    TaskRecord.video_id.ilike(pattern),
                    TaskRecord.file_path.ilike(pattern),
                )
            )
        return statement

    async def list_tasks(
        self,
        *,
        states: Iterable[TaskState] | None = None,
        query: str | None = None,
        failure_kind: FailureKind | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[DownloadTask]:
        statement = (
            self._base_select(states=states, query=query, failure_kind=failure_kind)
            .order_by(TaskRecord.position.asc(), TaskRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._db.session() as session:
            records = (await session.execute(statement)).scalars().all()
            return [_to_domain(record) for record in records]

    async def count_tasks(
        self,
        *,
        states: Iterable[TaskState] | None = None,
        query: str | None = None,
        failure_kind: FailureKind | None = None,
    ) -> int:
        statement = select(func.count())
        # On reprend les mêmes filtres en repartant de la sélection de base.
        base = self._base_select(states=states, query=query, failure_kind=failure_kind).subquery()
        statement = statement.select_from(base)
        async with self._db.session() as session:
            return int((await session.execute(statement)).scalar_one())

    async def set_positions(self, ordered_ids: Sequence[str]) -> None:
        async with self._db.session() as session:
            for rank, task_id in enumerate(ordered_ids):
                await session.execute(
                    update(TaskRecord).where(TaskRecord.id == task_id).values(position=rank)
                )

    async def delete_task(self, task_id: str) -> None:
        async with self._db.session() as session:
            await session.execute(delete(TaskRecord).where(TaskRecord.id == task_id))

    async def recover_interrupted(self) -> int:
        """Remet en file ce qui était en cours au moment d'un crash / d'une fermeture."""
        async with self._db.session() as session:
            statement = select(TaskRecord).where(
                TaskRecord.state.in_([s.value for s in _INTERRUPTED])
            )
            records = (await session.execute(statement)).scalars().all()
            for record in records:
                record.state = TaskState.QUEUED.value
                record.speed_bps = 0.0
                record.progress = 0.0
                record.updated_at = utc_now()
            return len(records)

    async def aggregate_stats(self) -> dict[str, int]:
        """Compteurs par état + volume total téléchargé (une seule requête groupée)."""
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(
                        TaskRecord.state,
                        func.count(),
                        func.coalesce(func.sum(TaskRecord.file_size), 0),
                    ).group_by(TaskRecord.state)
                )
            ).all()

        stats: dict[str, int] = {"total": 0, "bytes_done": 0}
        for state, count, size in rows:
            stats[str(state)] = int(count)
            stats["total"] += int(count)
            if state == TaskState.DONE.value:
                stats["bytes_done"] = int(size or 0)
        return stats

    # ------------------------------------------------------------------
    # Dédoublonnage
    # ------------------------------------------------------------------
    async def known_video_ids(self, ids: Iterable[str]) -> set[str]:
        wanted = [i for i in ids if i]
        if not wanted:
            return set()
        statement = select(TaskRecord.video_id).where(
            TaskRecord.video_id.in_(wanted), TaskRecord.state == TaskState.DONE.value
        )
        async with self._db.session() as session:
            return {row[0] for row in (await session.execute(statement)).all() if row[0]}

    async def find_by_hash(self, file_hash: str) -> DownloadTask | None:
        statement = select(TaskRecord).where(
            TaskRecord.file_hash == file_hash, TaskRecord.state == TaskState.DONE.value
        )
        async with self._db.session() as session:
            record = (await session.execute(statement)).scalars().first()
            return _to_domain(record) if record else None

    # ------------------------------------------------------------------
    # Comptes surveillés
    # ------------------------------------------------------------------
    async def list_watched(self, *, enabled_only: bool = False) -> list[WatchedAccount]:
        statement = select(WatchedRecord)
        if enabled_only:
            statement = statement.where(WatchedRecord.enabled.is_(True))
        async with self._db.session() as session:
            records = (await session.execute(statement)).scalars().all()
            return [
                WatchedAccount(
                    username=record.username,
                    enabled=record.enabled,
                    interval_minutes=record.interval_minutes,
                    last_checked_at=_aware(record.last_checked_at),
                    last_video_ids=json.loads(record.last_video_ids_json or "[]"),
                    options=_safe_options(record.options_json),
                )
                for record in records
            ]

    async def upsert_watched(self, account: WatchedAccount) -> None:
        async with self._db.session() as session:
            record = await session.get(WatchedRecord, account.username)
            if record is None:
                record = WatchedRecord(username=account.username)
                session.add(record)
            record.enabled = account.enabled
            record.interval_minutes = account.interval_minutes
            record.last_checked_at = account.last_checked_at
            record.last_video_ids_json = json.dumps(account.last_video_ids)
            record.options_json = account.options.model_dump_json()

    async def delete_watched(self, username: str) -> None:
        async with self._db.session() as session:
            await session.execute(
                delete(WatchedRecord).where(WatchedRecord.username == username.lstrip("@"))
            )


def _safe_options(raw: str) -> DownloadOptions:
    try:
        return DownloadOptions.model_validate_json(raw or "{}")
    except Exception:
        return DownloadOptions()
