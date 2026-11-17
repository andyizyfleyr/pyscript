"""Moteur SQLite asynchrone et fabrique de sessions.

SQLite n'accepte qu'**un seul écrivain à la fois**. Or TikVault fait vivre plusieurs
workers, la boucle de statistiques et la veille en parallèle. Deux garde-fous sont
donc mis en place :

* un verrou asyncio qui sérialise l'ouverture des sessions (plus de conflit de
  connexion ni d'avertissement « double checkin » du pool) ;
* le mode WAL, qui permet aux lectures de continuer pendant une écriture.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from tikvault.infrastructure.db.models import Base


def build_url(path: Path) -> str:
    """URL SQLAlchemy pour un fichier SQLite local (mémoire si ``:memory:``)."""
    if str(path) == ":memory:":
        return "sqlite+aiosqlite:///:memory:"
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{path}"


class Database:
    """Encapsule le moteur et la fabrique de sessions (injectée dans le dépôt)."""

    def __init__(self, path: Path | str = ":memory:", *, echo: bool = False) -> None:
        self._url = build_url(path) if not isinstance(path, str) else build_url(Path(path))
        self._engine: AsyncEngine = create_async_engine(
            self._url,
            echo=echo,
            # Une seule connexion réutilisée : SQLite gère très mal les écritures
            # concurrentes, et le verrou ci-dessous sérialise de toute façon les accès.
            poolclass=StaticPool,
            pool_pre_ping=True,
        )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )
        # Sérialisation des sessions : évite les conflits de connexion entre les
        # workers, la boucle de statistiques et la veille.
        self._lock = asyncio.Lock()
        _enable_wal(self._engine)

    @property
    def url(self) -> str:
        return self._url

    async def create_all(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Session exclusive : une seule à la fois, commit automatique, rollback sinon."""
        async with self._lock, self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self._engine.dispose()


def _enable_wal(engine: AsyncEngine) -> None:
    """Active WAL + foreign keys à chaque nouvelle connexion SQLite.

    WAL : les lectures ne bloquent plus les écritures (tableau de bord fluide pendant
    un lot). ``synchronous=NORMAL`` : bon compromis vitesse/sécurité pour un cache local.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
