"""Modèles ORM SQLAlchemy 2.0 (style déclaratif typé).

Trois tables :
  * ``tasks``    — la file d'attente **et** l'historique (même table, filtrée par état) ;
  * ``watched``  — les comptes surveillés ;
  * ``settings`` — réglages persistés sous forme clé/valeur JSON.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base déclarative du projet."""


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), default="video")
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # Progression
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    bytes_done: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int] = mapped_column(Integer, default=0)
    speed_bps: Mapped[float] = mapped_column(Float, default=0.0)
    eta_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Tentatives et erreurs
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    failure_kind: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Résultat
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    extractor: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Métadonnées vidéo (dénormalisées pour la recherche et l'affichage rapides)
    video_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Options figées à l'ajout (JSON Pydantic)
    options_json: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_tasks_state_position", "state", "position"),
        Index("ix_tasks_search", "title", "uploader_id"),
    )


class WatchedRecord(Base):
    __tablename__ = "watched"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_video_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    options_json: Mapped[str] = mapped_column(Text, default="{}")


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
