"""Modèles métier de TikVault (Pydantic v2).

Ces objets sont la « langue commune » entre les services, l'infrastructure et l'UI.
Aucune dépendance vers Qt, yt-dlp ou SQLAlchemy n'est autorisée ici.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Horodatage UTC conscient (utilisé partout pour éviter les mélanges de fuseaux)."""
    return datetime.now(tz=UTC)


def new_id() -> str:
    """Identifiant de tâche court et stable."""
    return uuid.uuid4().hex


class SourceType(StrEnum):
    """Nature de la source demandée par l'utilisateur."""

    VIDEO = "video"  # un lien de vidéo unique
    PROFILE = "profile"  # @utilisateur complet
    HASHTAG = "hashtag"  # #tag
    SOUND = "sound"  # musique / son
    PLAYLIST = "playlist"  # playlist ou collection
    UNKNOWN = "unknown"


class TaskState(StrEnum):
    """Cycle de vie d'une tâche de téléchargement."""

    QUEUED = "queued"  # en file d'attente
    FETCHING = "fetching"  # résolution des métadonnées
    DOWNLOADING = "downloading"
    POSTPROCESSING = "postprocessing"  # extraction audio, miniature, métadonnées
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # filtré ou doublon

    @property
    def is_terminal(self) -> bool:
        """État définitif (plus rien à faire sauf action utilisateur)."""
        return self in {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED, TaskState.SKIPPED}

    @property
    def is_active(self) -> bool:
        """La tâche consomme actuellement un worker."""
        return self in {TaskState.FETCHING, TaskState.DOWNLOADING, TaskState.POSTPROCESSING}


class FailureKind(StrEnum):
    """Classification des échecs — pilotée par :func:`tikvault.domain.errors.classify_error`."""

    PRIVATE = "private"
    DELETED = "deleted"
    GEOBLOCKED = "geoblocked"
    RATE_LIMITED = "rate_limited"
    LOGIN_REQUIRED = "login_required"
    NETWORK = "network"
    DISK_FULL = "disk_full"
    EXTRACTOR = "extractor"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class VideoMeta(BaseModel):
    """Métadonnées normalisées d'une vidéo, quel que soit l'extracteur."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    video_id: str
    url: str
    title: str = ""
    uploader: str = ""
    uploader_id: str = ""
    duration: float | None = None
    upload_date: date | None = None
    view_count: int | None = None
    like_count: int | None = None
    thumbnail: str | None = None
    ext: str = "mp4"
    source: str = "inconnu"  # nom de l'extracteur ayant fourni les données

    @field_validator("title", "uploader", "uploader_id", mode="before")
    @classmethod
    def _empty_if_none(cls, value: Any) -> Any:
        return "" if value is None else value

    @property
    def display_title(self) -> str:
        """Titre lisible, avec repli sur l'identifiant."""
        return self.title.strip() or f"vidéo {self.video_id}"


class FilterOptions(BaseModel):
    """Filtres métier appliqués *avant* téléchargement (économie de bande passante)."""

    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    min_duration: float | None = Field(default=None, ge=0)
    max_duration: float | None = Field(default=None, ge=0)
    min_views: int | None = Field(default=None, ge=0)
    max_items: int | None = Field(default=None, ge=1, description="Plafond par source.")

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.date_from,
                self.date_to,
                self.min_duration,
                self.max_duration,
                self.min_views,
                self.max_items,
            )
        )


class DownloadOptions(BaseModel):
    """Options de téléchargement d'un lot (modifiables depuis la page Réglages)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # --- Destination ------------------------------------------------------
    output_dir: str = "~/TikVault"
    filename_template: str = "{username}/{date}_{id}_{title}.mp4"

    # --- Qualité ----------------------------------------------------------
    best_quality: bool = True
    no_watermark: bool = True
    audio_only: bool = False
    audio_format: str = "mp3"  # mp3 | m4a | opus | flac | wav
    embed_thumbnail: bool = True
    embed_metadata: bool = True

    # --- Performance ------------------------------------------------------
    max_concurrency: int = Field(default=3, ge=1, le=10)
    rate_limit_kbps: int | None = Field(default=None, ge=1, description="Ko/s par tâche.")
    max_attempts: int = Field(default=4, ge=1, le=10)
    socket_timeout: float = Field(default=30.0, gt=0)

    # --- Robustesse -------------------------------------------------------
    rotate_proxies: bool = True
    rotate_user_agents: bool = True

    # --- Filtres ----------------------------------------------------------
    filters: FilterOptions = Field(default_factory=FilterOptions)

    @field_validator("audio_format")
    @classmethod
    def _check_audio_format(cls, value: str) -> str:
        allowed = {"mp3", "m4a", "opus", "flac", "wav", "aac"}
        if value.lower() not in allowed:
            msg = f"Format audio {value!r} non pris en charge (attendu : {sorted(allowed)})"
            raise ValueError(msg)
        return value.lower()

    @property
    def final_extension(self) -> str:
        """Extension attendue du fichier produit."""
        return self.audio_format if self.audio_only else "mp4"


class DownloadTask(BaseModel):
    """Une unité de travail de la file de téléchargement."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    url: str
    source_type: SourceType = SourceType.VIDEO
    state: TaskState = TaskState.QUEUED
    position: int = 0

    # Progression
    progress: float = 0.0  # 0.0 → 1.0
    bytes_done: int = 0
    bytes_total: int = 0
    speed_bps: float = 0.0
    eta_seconds: float | None = None

    # Tentatives / erreurs
    attempts: int = 0
    max_attempts: int = 4
    failure_kind: FailureKind | None = None
    error_message: str | None = None
    skip_reason: str | None = None

    # Résultat
    file_path: str | None = None
    file_hash: str | None = None
    file_size: int = 0
    meta: VideoMeta | None = None
    extractor: str | None = None

    # Horodatages
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # Options figées au moment de l'ajout (permet de changer les réglages sans casser la file)
    options: DownloadOptions = Field(default_factory=DownloadOptions)

    @property
    def percent(self) -> int:
        return max(0, min(100, round(self.progress * 100)))

    @property
    def retryable(self) -> bool:
        return self.attempts < self.max_attempts


class BatchStats(BaseModel):
    """Statistiques agrégées affichées sur le tableau de bord."""

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    queued: int = 0
    running: int = 0
    paused: int = 0
    done: int = 0
    failed: int = 0
    cancelled: int = 0
    skipped: int = 0
    bytes_downloaded: int = 0
    remaining_bytes: int = 0
    disk_used: int = 0
    speed_bps: float = 0.0
    elapsed_seconds: float = 0.0

    @property
    def eta_seconds(self) -> float | None:
        """Temps restant estimé à partir du débit moyen constaté."""
        if self.speed_bps <= 0 or self.remaining_bytes <= 0:
            return None
        return self.remaining_bytes / self.speed_bps

    @property
    def completion(self) -> float:
        if self.total == 0:
            return 0.0
        finished = self.done + self.failed + self.cancelled + self.skipped
        return finished / self.total


class WatchedAccount(BaseModel):
    """Compte surveillé : télécharge automatiquement les nouvelles vidéos."""

    model_config = ConfigDict(extra="forbid")

    username: str
    interval_minutes: int = Field(default=30, ge=1)
    enabled: bool = True
    last_checked_at: datetime | None = None
    last_video_ids: list[str] = Field(default_factory=list)
    options: DownloadOptions = Field(default_factory=DownloadOptions)

    @field_validator("username")
    @classmethod
    def _strip_at(cls, value: str) -> str:
        return value.strip().lstrip("@")
